import pytest

from review_fabric.errors import (
    DeniedMutationError,
    InvalidReviewerOutputError,
    InvalidReviewPackageError,
    PolicyRejectionError,
)


@pytest.mark.parametrize(
    "error_type",
    [
        InvalidReviewPackageError,
        InvalidReviewerOutputError,
        PolicyRejectionError,
        DeniedMutationError,
    ],
)
def test_protocol_errors_have_a_serializable_record(error_type: type[Exception]) -> None:
    error = error_type("invalid input")

    assert error.to_record() == {
        "error_type": error_type.__name__,
        "message": "invalid input",
    }
