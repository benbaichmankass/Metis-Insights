# ML training policy

## Claude does locally

- Edit training scripts.
- Create configs.
- Add dry-run modes.
- Validate argument parsing.
- Build Colab/HF automation.

## Claude does not do locally by default

- Train models.
- Download large datasets.
- Run long backtests.
- Commit generated model artifacts.

## Training script pattern

Every new training entry point should support:

```bash
python train.py --config config.yaml --dry-run
python train.py --config config.yaml --output-dir /content/drive/MyDrive/...
```
