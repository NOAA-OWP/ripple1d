import pytest

from ripple1d.ops.ras_conflate import conflate_model
from tests.conflation_tests.classes import ConflationFile, PathManager
from tests.conflation_tests.consts import TESTS
from tests.conflation_tests.plotting import plot_conflation


@pytest.mark.parametrize("ras_dir_name", TESTS)
def test_conflation(ras_dir_name: str, generate_plots: bool = False):
    """Run a specific test case."""
    pm = PathManager(ras_dir_name)
    conflate_model(pm.ras_dir, pm.ras_model_name, pm.nwm_dict, False)
    if generate_plots:
        plot_conflation(pm.ras_path, pm.nwm_path, pm.conflation_file)
    ConflationFile(pm.conflation_file) == ConflationFile(pm.rubric_file)  # Validation


def run_all():
    """Run all conflation tests."""
    for test in ["test_n"]:
        test_conflation(test, generate_plots=True)


if __name__ == "__main__":
    run_all()
