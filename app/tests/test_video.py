import os
import tempfile

import cv2
import numpy as np
import pytest


def create_test_video(filepath: str, duration_seconds: float, fps: int = 30, width: int = 64, height: int = 64) -> None:
    """Create a synthetic test video with colored frames."""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(filepath, fourcc, fps, (width, height))

    total_frames = int(duration_seconds * fps)
    for i in range(total_frames):
        # Create a frame with varying colors
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:, :, 0] = (i * 3) % 256  # Blue channel
        frame[:, :, 1] = (i * 5) % 256  # Green channel
        frame[:, :, 2] = (i * 7) % 256  # Red channel
        out.write(frame)

    out.release()


@pytest.fixture
def short_video():
    """Create a 2-second test video."""
    fd, filepath = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    create_test_video(filepath, duration_seconds=2.0, fps=30)
    yield filepath
    if os.path.exists(filepath):
        os.remove(filepath)


@pytest.fixture
def long_video():
    """Create a 90-second test video (exceeds default 60s limit)."""
    fd, filepath = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    create_test_video(filepath, duration_seconds=90.0, fps=10)  # Lower fps to speed up creation
    yield filepath
    if os.path.exists(filepath):
        os.remove(filepath)


class TestGetVideoDuration:
    def test_returns_correct_duration(self, short_video):
        import video
        duration = video.get_video_duration(short_video)
        assert 1.9 <= duration <= 2.1  # Allow small tolerance

    def test_returns_correct_duration_long_video(self, long_video):
        import video
        duration = video.get_video_duration(long_video)
        assert 89.0 <= duration <= 91.0

    def test_returns_zero_for_invalid_file(self):
        import video
        duration = video.get_video_duration("/nonexistent/file.mp4")
        assert duration == 0.0


class TestCheckVideoDuration:
    def test_short_video_passes(self, short_video):
        import video
        assert video.check_video_duration(short_video) is False  # False = passes check

    def test_long_video_fails(self, long_video):
        import video
        assert video.check_video_duration(long_video) is True  # True = exceeds limit


class TestExtractVideoFrames:
    def test_extracts_frames_at_interval(self, short_video):
        import video
        frames = video.extract_video_frames(short_video, interval=1.0)
        try:
            # 2-second video at 1-second interval should yield ~2-3 frames
            assert 2 <= len(frames) <= 3
            # Verify frames are actual files
            for frame_path in frames:
                assert os.path.exists(frame_path)
                assert frame_path.endswith(".jpg")
        finally:
            for frame_path in frames:
                if os.path.exists(frame_path):
                    os.remove(frame_path)

    def test_extracts_more_frames_with_shorter_interval(self, short_video):
        import video
        frames = video.extract_video_frames(short_video, interval=0.5)
        try:
            # 2-second video at 0.5-second interval should yield ~4-5 frames
            assert 4 <= len(frames) <= 5
        finally:
            for frame_path in frames:
                if os.path.exists(frame_path):
                    os.remove(frame_path)

    def test_returns_empty_list_for_invalid_file(self):
        import video
        frames = video.extract_video_frames("/nonexistent/file.mp4", interval=1.0)
        assert frames == []


class TestCheckVideoNudityFilter:
    def test_returns_false_when_filter_disabled(self, short_video, monkeypatch):
        import video
        monkeypatch.setattr("video.settings.NUDE_FILTER_MAX_THRESHOLD", None)
        monkeypatch.setattr("video.nude_classifier", None)
        result = video.check_video_nudity_filter(short_video)
        assert result is False

    def test_returns_false_for_safe_video(self, short_video, monkeypatch):
        import video

        # Mock the classifier to return safe values for batch classification
        class MockClassifier:
            def classify(self, filepaths: list[str]) -> dict[str, dict[str, float]]:
                return {fp: {"unsafe": 0.1} for fp in filepaths}

        monkeypatch.setattr("video.settings.NUDE_FILTER_MAX_THRESHOLD", 0.5)
        monkeypatch.setattr("video.nude_classifier", MockClassifier())

        result = video.check_video_nudity_filter(short_video)
        assert result is False

    def test_returns_true_for_unsafe_video(self, short_video, monkeypatch):
        import video

        # Mock the classifier to return unsafe values for batch classification
        class MockClassifier:
            def classify(self, filepaths: list[str]) -> dict[str, dict[str, float]]:
                return {fp: {"unsafe": 0.9} for fp in filepaths}

        monkeypatch.setattr("video.settings.NUDE_FILTER_MAX_THRESHOLD", 0.5)
        monkeypatch.setattr("video.nude_classifier", MockClassifier())

        result = video.check_video_nudity_filter(short_video)
        assert result is True

    def test_cleans_up_temp_frames(self, short_video, monkeypatch):
        import video

        extracted_frames: list[str] = []

        original_extract = video.extract_video_frames

        def tracking_extract(filepath: str, interval: float) -> list[str]:
            frames = original_extract(filepath, interval)
            extracted_frames.extend(frames)
            return frames

        # Mock the classifier to return safe values for batch classification
        class MockClassifier:
            def classify(self, filepaths: list[str]) -> dict[str, dict[str, float]]:
                return {fp: {"unsafe": 0.1} for fp in filepaths}

        monkeypatch.setattr("video.extract_video_frames", tracking_extract)
        monkeypatch.setattr("video.settings.NUDE_FILTER_MAX_THRESHOLD", 0.5)
        monkeypatch.setattr("video.nude_classifier", MockClassifier())

        video.check_video_nudity_filter(short_video)

        # Verify all temp frames were cleaned up
        for frame_path in extracted_frames:
            assert not os.path.exists(frame_path)
