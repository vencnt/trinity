from abc import ABC, abstractmethod
from typing import Sequence

from eth_typing import BLSPubkey, BLSSignature, Hash32


class BaseBLSBackend(ABC):
    @staticmethod
    @abstractmethod
    def SkToPk(SK: int) -> BLSPubkey:
        ...

    @staticmethod
    @abstractmethod
    def Sign(SK: int, message: Hash32) -> BLSSignature:
        ...

    @staticmethod
    @abstractmethod
    def Verify(PK: BLSPubkey, message: Hash32, signature: BLSSignature) -> bool:
        ...

    @staticmethod
    @abstractmethod
    def Aggregate(signatures: Sequence[BLSSignature]) -> BLSSignature:
        ...

    @staticmethod
    @abstractmethod
    def FastAggregateVerify(
        PKs: Sequence[BLSPubkey], message: Hash32, signature: BLSSignature
    ) -> bool:
        ...
