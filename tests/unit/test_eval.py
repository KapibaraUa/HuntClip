import pytest
from scripts.eval import (
    EvalMetrics,
    ExpertClip,
    calculate_iou,
    evaluate_stream,
    render_markdown_report,
)


def test_calculate_iou() -> None:
    # Exact match: IoU = 1.0
    assert calculate_iou(10.0, 20.0, 10.0, 20.0) == 1.0

    # Half overlap: [10, 20] and [15, 25] -> intersection = 5, union = 15 -> IoU = 1/3
    assert pytest.approx(calculate_iou(10.0, 20.0, 15.0, 25.0), 0.01) == 0.333

    # Overlap 10 to 30 with 15 to 25 -> intersection = 10, union = 20 -> IoU = 0.5
    assert calculate_iou(10.0, 30.0, 15.0, 25.0) == 0.5

    # No overlap
    assert calculate_iou(10.0, 20.0, 25.0, 30.0) == 0.0

    # Zero duration
    assert calculate_iou(10.0, 10.0, 10.0, 10.0) == 0.0


def test_evaluate_stream_metrics() -> None:
    expert_clips = [
        ExpertClip(id="e1", start_s=100.0, end_s=130.0, category="funny"),
        ExpertClip(id="e2", start_s=200.0, end_s=230.0, category="game_moment"),
    ]

    predicted_clips = [
        # Match with e1 (IoU >= 0.5): start 100.2, end 129.8, raw 98.0, 132.0
        {
            "id": "p1",
            "start_s": 100.2,
            "end_s": 129.8,
            "raw_start_s": 98.0,
            "raw_end_s": 132.0,
            "score": 9,
        },
        # False positive
        {
            "id": "p2",
            "start_s": 500.0,
            "end_s": 520.0,
            "raw_start_s": 500.0,
            "raw_end_s": 520.0,
            "score": 6,
        },
    ]

    metrics = evaluate_stream(
        expert_clips=expert_clips,
        predicted_clips=predicted_clips,
        stream_id="test_stream",
        genre="gameplay",
        duration_s=1800.0,
        latency_s=300.0,
        cost_usd=0.15,
        iou_threshold=0.5,
    )

    # 1 TP (e1 matched with p1), 1 FP (p2), 1 FN (e2 not matched)
    assert metrics.expert_count == 2
    assert metrics.predicted_count == 2
    assert metrics.true_positives == 1
    assert metrics.recall == 0.5
    assert metrics.precision == 0.5
    assert metrics.f1 == 0.5

    # Boundary error after: (|100.2 - 100.0| + |129.8 - 130.0|) / 2 = 0.2
    assert pytest.approx(metrics.mean_boundary_error_s, 0.01) == 0.2
    # Boundary error before: (|98.0 - 100.0| + |132.0 - 130.0|) / 2 = 2.0
    assert pytest.approx(metrics.mean_boundary_error_before_s, 0.01) == 2.0

    # Speedup: 1800 / 300 = 6.0x
    assert metrics.speedup_x == 6.0
    # Cost per hour: 0.15 / (1800 / 3600) = 0.30 $/h
    assert metrics.cost_per_hour_usd == 0.30


def test_render_markdown_report() -> None:
    metric = EvalMetrics(
        stream_id="test_01",
        genre="gameplay",
        duration_s=1800.0,
        expert_count=4,
        predicted_count=4,
        true_positives=4,
        recall=1.0,
        precision=1.0,
        f1=1.0,
        mean_boundary_error_s=0.3,
        mean_boundary_error_before_s=2.1,
        latency_s=300.0,
        speedup_x=6.0,
        cost_usd=0.15,
        cost_per_hour_usd=0.30,
    )
    report = render_markdown_report([metric], timestamp="20261020_230000", dry_run=True)
    assert "Recall" in report
    assert "Precision" in report
    assert "Mean Boundary Error" in report
    assert "PASS" in report
