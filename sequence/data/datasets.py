import nltk
from sequence.data.utils import Dataset, Language, DatasetEager
from urllib import request
import os
import logging
from pyunpack import Archive, PatoolError
import pandas as pd
import numpy as np
import pickle


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def brown(dataset_kwargs={}):
    """
    Parameters
    ----------
    dataset_kwargs : dict
        Used to initialize sequence.data.utils.Dataset    dataset_kwargs

    Returns
    -------
    (ds, lang) : tuple[
                    sequence.data.utils.Dataset,
                    sequence.data.utils.Language
                    ]
    """
    nltk.download("brown")
    ds = DatasetEager(nltk.corpus.brown.sents(), **dataset_kwargs)
    return ds, ds.language


def treebank(dataset_kwargs={}):
    """
    Parameters
    ----------
    dataset_kwargs : dict
        Used to initialize sequence.data.utils.Dataset    dataset_kwargs

    Returns
    -------
    (ds, lang) : tuple[
                    sequence.data.utils.Dataset,
                    sequence.data.utils.Language
                    ]
    """
    nltk.download("treebank")
    ds = DatasetEager(nltk.corpus.treebank.sents(), **dataset_kwargs)
    return ds, ds.language


def download_and_unpack_yoochoose(storage_dir):
    fp = os.path.join(storage_dir, "yoochoose-data.7z")
    if not os.path.isfile(fp):
        logger.info("Downloading Yoochoose dataset...")
        url = "https://s3-eu-west-1.amazonaws.com/yc-rdata/yoochoose-data.7z"
        request.urlretrieve(url, fp)
    else:
        logger.info((f"Yoochoose dataset already exists in {fp}"))

    ds = os.path.join(storage_dir, "yoochoose-data")
    if not os.path.isdir(ds):
        logger.info((f"Unpacking zip archive"))
        os.makedirs(ds)

        try:
            Archive(fp).extractall(ds)
        except PatoolError as e:
            logger.error(
                f"{e}\nInstall a system application to process 7zip files, such as p7zip."
            )
            os.removedirs(ds)


def yoochoose(
    storage_dir,
    nrows=None,
    min_unique=5,
    skiprows=None,
    div64=False,
    test=False,
    cache=True,
    dataset_kwargs={},
    return_agg=False,
    filter_unique=True,
):
    """

    Parameters
    ----------
    storage_dir : str
        Directory path
    nrows : Union[None, int]
        Take only n_rows from the dataset.
    min_unique : int
        Items that occur less than min_unique are removed.
    skiprows : Union[None, int]
        Skip rows from csv.
    div64 : bool
        Load yoochoose 1/64
    test : bool
        Load test set
    cache : bool
        Cache pickled sequence.data.utils.Dataset in storage_dir
    dataset_kwargs : dict
        Used to initialize sequence.data.utils.Dataset
    return_agg : bool
        Return groupby aggregation.
    filter_unique : bool

    Returns
    -------
    (ds, lang) : tuple[
                    sequence.data.utils.Dataset,
                    sequence.data.utils.Language
                    ]
    """

    if cache:
        cached_file = os.path.join(storage_dir, "yoochoose-ds.pkl")
        if os.path.isfile(cached_file):
            with open(cached_file, "rb") as f:
                ds = pickle.load(f)
                ds.data = ds.data.rechunk("auto")
            return ds, ds.language

    if test:
        fn = "yoochoose-data/yoochoose-test.dat"
    else:
        fn = "yoochoose-data/yoochoose-clicks.dat"

    file_name = os.path.join(storage_dir, fn)

    if not os.path.isfile(file_name):
        download_and_unpack_yoochoose(storage_dir)

    logger.info("Read dataset in memory")
    df = pd.read_csv(
        file_name,
        names=["session_id", "timestamp", "item_id", "category"],
        usecols=["session_id", "timestamp", "item_id"],
        dtype={"session_id": np.int32, "timestamp": "str", "item_id": np.str},
        nrows=nrows,
        skiprows=skiprows,
        parse_dates=["timestamp"],
    )
    if filter_unique:
        logger.info(f"Remove items that occur < {min_unique} times")
        # Series of item_id -> counts
        item_n_unique = df["item_id"].value_counts()

        # Filter counts < k
        item_n_unique = item_n_unique[item_n_unique >= min_unique]

        # Create df[item_id, counts]
        item_n_unique = (
            item_n_unique.to_frame("counts")
            .reset_index()
            .rename(columns={"index": "item_id"})
        )
        df = df.merge(item_n_unique, how="inner", on="item_id").drop(columns="counts")
        del item_n_unique

    logger.info("Drop sessions of length 1")
    valid_sessions = (
        df.groupby("session_id")["item_id"]
        .agg("count")
        .to_frame()
        .reset_index()
        .rename(columns={"index": "session_id", "item_id": "count"})
    )
    valid_sessions = valid_sessions[valid_sessions["count"] > 1]
    df = df.merge(valid_sessions, how="inner", on="session_id")
    del valid_sessions
    df = df.sort_values("timestamp")

    df_test = df.iloc[-71233:]
    df = df.iloc[:-71233]
    df_test = df_test[df_test["item_id"].isin(df["item_id"])]

    if div64:
        logger.info("Get 1/64 split")
        sessions = df.drop_duplicates("session_id")
        sessions = sessions.assign(timestamp=pd.to_datetime(sessions["timestamp"]))
        sessions = sessions.sort_values("timestamp")[["session_id"]]
        n = sessions.shape[0] // 60  # div by 60 is closer to paper count
        sessions = sessions.iloc[-n:]

        df = df.merge(sessions, how="inner", on="session_id")[
            ["session_id", "timestamp", "item_id"]
        ]
        del sessions

    df = df[["session_id", "item_id"]]
    df_test = df_test[["session_id", "item_id"]]
    logger.info("Aggregate sessions")
    agg = df.groupby("session_id").agg(list)
    agg_test = df_test.groupby("session_id").agg(list)

    logger.info("Avg length: {:.2f}".format(agg["item_id"].apply(len).mean()))
    del df
    del df_test

    if return_agg:
        return agg

    language = Language(lower=False, remove_punctuation=False)
    ds = Dataset([r[1] for r in agg.itertuples()], language, **dataset_kwargs)
    ds_test = Dataset([r[1] for r in agg_test.itertuples()], language, **dataset_kwargs)

    if cache:
        with open(cached_file, "wb") as f:
            pickle.dump(ds, f)
        with open(os.path.join(storage_dir, "yoochoose-ds-test.pkl"), "wb") as f:
            pickle.dump(ds_test, f)

    return ds, ds.language
