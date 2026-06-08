"""NumProof — deterministic numeric & financial verification, as a thin API client.

    from numproof import NumProof
    np = NumProof.from_env()
    np.verify("gross margin is 60% when gross profit is 600 and revenue is 1000")

The verification engine is the hosted NumProof service; this package only calls it.
"""
from .client import NumProof, VerifyClient, NumProofAPIError

__version__ = "0.1.0"
__all__ = ["NumProof", "VerifyClient", "NumProofAPIError", "__version__"]
