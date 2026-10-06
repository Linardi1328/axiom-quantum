from __future__ import annotations

from datetime import date
from typing import Protocol

import pandas as pd


class ResearchSupervisedMetadataLike(Protocol):
    @property
    def source_market_data_checksum(self) -> str: ...

    @property
    def feature_schema_version(self) -> str: ...

    @property
    def label_schema_version(self) -> str: ...

    @property
    def first_session(self) -> date: ...

    @property
    def last_session(self) -> date: ...


class ResearchSupervisedDatasetLike(Protocol):
    @property
    def features(self) -> pd.DataFrame: ...

    @property
    def labels(self) -> pd.DataFrame: ...

    @property
    def metadata(self) -> ResearchSupervisedMetadataLike: ...
