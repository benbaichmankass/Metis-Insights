"""Refusal paths of scripts/ops/oci_retire_orphan_volume.py (no OCI calls).

The operator approved "Back up, then delete" for ONE orphaned volume
(PI-20260929-CMXYTHSP-0002). A delete is irreversible, so every refusal path
must be shown to stop BEFORE delete_volume is called.
"""
from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace as NS

import pytest

_P = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "ops" / "oci_retire_orphan_volume.py"
_spec = importlib.util.spec_from_file_location("oci_retire_orphan_volume", _P)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

VID = "ocid1.volume.oc1.eu-paris-1.target"


def _vol(**kw):
    base = dict(id=VID, display_name=m.EXPECTED_NAME, size_in_gbs=m.EXPECTED_SIZE_GB,
                time_created=m.EXPECTED_CREATED, lifecycle_state="AVAILABLE")
    base.update(kw)
    return NS(**base)


class FakeApi:
    def __init__(self, vols=None, atts=None, att_error=None, atts_second=None,
                 backup_states=("CREATING", "AVAILABLE"), create_error=None):
        self.vols = [_vol()] if vols is None else vols
        self.atts = atts or []
        self.atts_second = atts_second
        self.att_error = att_error
        self.att_calls = 0
        self.backup_states = list(backup_states)
        self.create_error = create_error
        self.deleted = []
        self.backup_created = False

    def list_volumes(self, comp):
        return self.vols

    def list_attachments(self, comp, vol_id):
        self.att_calls += 1
        if self.att_error:
            raise self.att_error
        if self.atts_second is not None and self.att_calls > 1:
            return self.atts_second
        return self.atts

    def create_backup(self, vol_id, name):
        if self.create_error:
            raise self.create_error
        self.backup_created = True
        return NS(id="ocid1.volumebackup.x", lifecycle_state="REQUEST_RECEIVED", display_name=name)

    def get_backup(self, bid):
        st = self.backup_states.pop(0) if len(self.backup_states) > 1 else self.backup_states[0]
        return NS(id=bid, lifecycle_state=st, display_name=m.BACKUP_NAME)

    def delete_volume(self, vol_id):
        self.deleted.append(vol_id)

    def volume_state(self, vol_id):
        return "TERMINATED"


def _run(api, mode="execute", volume_id=VID, **kw):
    clock = {"t": 0.0}

    def now():
        return clock["t"]

    def sleep(s):
        clock["t"] += s

    return m.run(api, mode=mode, volume_id=volume_id, compartments=["c"],
                 backup_timeout_s=kw.get("backup_timeout_s", 600),
                 delete_timeout_s=60, sleep=sleep, now=now)


def test_happy_path_backs_up_then_deletes():
    api = FakeApi()
    assert _run(api) == 0
    assert api.backup_created and api.deleted == [VID]


def test_plan_mode_never_mutates():
    api = FakeApi()
    assert _run(api, mode="plan", volume_id="") == 0
    assert not api.backup_created and api.deleted == []


def test_attachment_present_refuses_before_backup():
    api = FakeApi(atts=[NS(lifecycle_state="ATTACHED", instance_id="ocid1.instance.x")])
    with pytest.raises(m.Refused) as e:
        _run(api)
    assert e.value.code == 2
    assert not api.backup_created and api.deleted == []


def test_detached_record_also_refuses():
    api = FakeApi(atts=[NS(lifecycle_state="DETACHED", instance_id="ocid1.instance.x")])
    with pytest.raises(m.Refused):
        _run(api)
    assert api.deleted == []


def test_attachment_listing_error_refuses():
    api = FakeApi(att_error=RuntimeError("403 NotAuthorized"))
    with pytest.raises(m.Refused) as e:
        _run(api)
    assert "could not list attachments" in str(e.value)
    assert not api.backup_created and api.deleted == []


def test_attachment_appearing_after_backup_refuses_and_keeps_backup():
    api = FakeApi(atts_second=[NS(lifecycle_state="ATTACHING", instance_id="ocid1.instance.x")])
    with pytest.raises(m.Refused) as e:
        _run(api)
    assert e.value.code == 3
    assert api.backup_created and api.deleted == []


@pytest.mark.parametrize("final", ["FAULTY", "TERMINATED"])
def test_backup_failed_refuses_without_delete(final):
    api = FakeApi(backup_states=("CREATING", final))
    with pytest.raises(m.Refused) as e:
        _run(api)
    assert "NOT deleted" in str(e.value)
    assert api.deleted == []


def test_backup_never_available_times_out_without_delete():
    api = FakeApi(backup_states=("CREATING",))
    with pytest.raises(m.Refused) as e:
        _run(api, backup_timeout_s=120)
    assert "not AVAILABLE" in str(e.value)
    assert api.deleted == []


def test_backup_create_error_refuses():
    api = FakeApi(create_error=RuntimeError("LimitExceeded"))
    with pytest.raises(m.Refused):
        _run(api)
    assert api.deleted == []


def test_volume_id_mismatch_refuses():
    api = FakeApi()
    with pytest.raises(m.Refused):
        _run(api, volume_id="ocid1.volume.oc1.eu-paris-1.other")
    assert not api.backup_created and api.deleted == []


@pytest.mark.parametrize("override", [
    {"display_name": "ict-bot-arm-data"},
    {"size_in_gbs": 50},
    {"time_created": m.EXPECTED_CREATED.replace(second=1)},
])
def test_pinned_identity_mismatch_refuses(override):
    api = FakeApi(vols=[_vol(**override)])
    with pytest.raises(m.Refused):
        _run(api)
    assert api.deleted == []


def test_two_matching_volumes_refuses():
    api = FakeApi(vols=[_vol(), _vol(id="ocid1.volume.dup")])
    with pytest.raises(m.Refused):
        _run(api)
    assert api.deleted == []


def test_not_available_volume_refuses():
    api = FakeApi(vols=[_vol(lifecycle_state="FAULTY")])
    with pytest.raises(m.Refused):
        _run(api)
    assert api.deleted == []
