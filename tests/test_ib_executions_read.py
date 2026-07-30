"""``IBClient.executions`` — the broker-truth read behind the IB fills pull.

The contract under test is almost entirely about ONE distinction:

    ``None`` (could-not-read)  is  NOT  ``[]`` (confirmed no fills)

Conflating the two is the exact error class the 2026-07-30 provenance work
exists to end — recording an absence of measurement as evidence of none. A
wedged Gateway must never look like a clean, empty trading day.

Mirrors ``test_ib_naked_rearm``'s stub idiom (a fake ``ib`` object + a
monkeypatched ``connect``); the sandbox cannot reach a live Gateway, so broker
acceptance itself is out of scope here.
"""
from __future__ import annotations

from src.units.accounts.ib_client import IBClient


class _Execution:
    def __init__(self, exec_id, acct=None):
        self.execId = exec_id
        self.acctNumber = acct


class _Fill:
    def __init__(self, exec_id, acct=None):
        self.execution = _Execution(exec_id, acct)


class _FakeIB:
    """Minimal stub exercising the sync ``reqExecutions`` fallback path."""

    def __init__(self, fills=None, raises=False):
        self._fills = fills or []
        self._raises = raises
        self.calls = 0
        self.last_filter = "__unset__"

    def reqExecutions(self, exec_filter=None):
        self.calls += 1
        self.last_filter = exec_filter
        if self._raises:
            raise RuntimeError("gateway wedged — execDetailsEnd never arrived")
        return list(self._fills)


def _ib(monkeypatch, fake, account="DUQ325724"):
    client = IBClient(port=4002, client_id=497, account=account, symbol="MES",
                      _ib_factory=lambda: fake)
    monkeypatch.setattr(client, "connect", lambda: fake)
    return client


def test_returns_fills_on_a_clean_read(monkeypatch):
    fake = _FakeIB([_Fill("a", "DUQ325724"), _Fill("b", "DUQ325724")])
    out = _ib(monkeypatch, fake).executions("20260730-00:00:00")
    assert [f.execution.execId for f in out] == ["a", "b"]
    assert fake.calls == 1


def test_empty_window_is_an_empty_list_not_none(monkeypatch):
    """A confirmed-clean read with no executions is genuine data."""
    out = _ib(monkeypatch, _FakeIB([])).executions("20260730-00:00:00")
    assert out == []
    assert out is not None


def test_read_failure_is_none_not_empty(monkeypatch):
    """The whole point: a wedged Gateway must not read as 'no fills'."""
    out = _ib(monkeypatch, _FakeIB(raises=True)).executions("20260730-00:00:00")
    assert out is None


def test_connect_failure_is_none(monkeypatch):
    """Breaker open / connect failed ⇒ cannot read ⇒ None, never raises."""
    client = IBClient(port=4002, client_id=497, account="DUQ", symbol="MES",
                      _ib_factory=lambda: _FakeIB())

    def _boom():
        raise RuntimeError("circuit breaker open")

    monkeypatch.setattr(client, "connect", _boom)
    assert client.executions("20260730-00:00:00") is None


def test_blank_since_is_refused(monkeypatch):
    """An unbounded reqExecutions is never issued by accident."""
    fake = _FakeIB([_Fill("a")])
    c = _ib(monkeypatch, fake)
    assert c.executions("") is None
    assert c.executions("   ") is None
    assert fake.calls == 0


def test_other_accounts_on_a_shared_login_are_filtered_out(monkeypatch):
    """A multi-account Gateway login reports every account; attributing another
    account's realised PnL to this one would fabricate money."""
    fake = _FakeIB([
        _Fill("mine", "DUQ325724"),
        _Fill("theirs", "U25907316"),
    ])
    out = _ib(monkeypatch, fake, account="DUQ325724").executions("20260730-00:00:00")
    assert [f.execution.execId for f in out] == ["mine"]


def test_fill_without_account_number_is_kept(monkeypatch):
    """The single-account case reports no acctNumber; dropping it would
    silently lose real fills."""
    fake = _FakeIB([_Fill("a", None)])
    out = _ib(monkeypatch, fake, account="DUQ325724").executions("20260730-00:00:00")
    assert [f.execution.execId for f in out] == ["a"]


def test_no_configured_account_returns_everything(monkeypatch):
    fake = _FakeIB([_Fill("a", "X"), _Fill("b", "Y")])
    out = _ib(monkeypatch, fake, account=None).executions("20260730-00:00:00")
    assert len(out) == 2


def test_malformed_fill_does_not_break_the_read(monkeypatch):
    class _Bad:
        @property
        def execution(self):
            raise ValueError("corrupt")

    fake = _FakeIB([_Bad(), _Fill("good", "DUQ325724")])
    out = _ib(monkeypatch, fake, account="DUQ325724").executions("20260730-00:00:00")
    # The malformed row has no readable account ⇒ kept (not silently dropped),
    # and crucially the GOOD fill still comes back.
    assert any(getattr(f, "execution", None) is not None
               and getattr(f.execution, "execId", None) == "good"
               for f in out if not isinstance(f, _Bad))
