"""register.evaluate 单元测试：配准质量指标提取。"""
from types import SimpleNamespace

import pytest

from h3d.register import (
    correspondence_count,
    evaluate_registration,
    fitness,
    inlier_rmse,
)


def test_fitness_and_rmse():
    r = SimpleNamespace(fitness=0.98, inlier_rmse=0.001,
                        correspondence_set=[(0, 0)])
    assert fitness(r) == pytest.approx(0.98)
    assert inlier_rmse(r) == pytest.approx(0.001)


def test_correspondence_count():
    r = SimpleNamespace(fitness=0.98, inlier_rmse=0.001,
                        correspondence_set=[(0, 0), (1, 1), (2, 2)])
    assert correspondence_count(r) == 3
    r_no_cs = SimpleNamespace(fitness=0.98, inlier_rmse=0.001)  # 无对应集
    assert correspondence_count(r_no_cs) == 0


def test_evaluate_registration_dict():
    r = SimpleNamespace(fitness=0.95, inlier_rmse=0.002,
                        correspondence_set=[(0, 0)])
    m = evaluate_registration(r)
    assert m == {"fitness": 0.95, "inlier_rmse": 0.002, "correspondence_count": 1}


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
