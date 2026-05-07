from datasets import Dataset
import pandas as pd
def push_ict_signals(csv_path="signals.csv", repo="bentzbk/ict-signals-dataset"):
 df = pd.read_csv(csv_path)
 ds = Dataset.from_pandas(df)
 ds.push_to_hub(repo)
if __name__ == "__main__":
    push_ict_signals()
