"""Parser tests.

These lock down the riskiest logic in the package. The compositor's wire format
has no quoting, and `title`/`app_id` come from Wayland clients, so these cases
are the difference between a working bridge and silent corruption.
"""

import pytest

from pi_panel_mqtt.models import ProtocolError, Status, View


class TestViewParsing:
    def test_plain_row(self):
        v = View.parse(
            "id=0 name=main app_id=main title=shell active=true mapped=true pid=1304"
        )
        assert (v.id, v.name, v.app_id, v.title) == (0, "main", "main", "shell")
        assert v.active is True and v.mapped is True and v.pid == 1304

    def test_null_placeholders_become_none(self):
        v = View.parse(
            "id=2 name=(none) app_id=(none) title=(none) "
            "active=false mapped=false pid=0"
        )
        assert v.name is None and v.app_id is None and v.title is None

    def test_title_containing_spaces(self):
        """The case that breaks a naive split() — and the common one."""
        v = View.parse(
            "id=1 name=browser app_id=firefox "
            "title=Some Page - Mozilla Firefox "
            "active=true mapped=true pid=999"
        )
        assert v.title == "Some Page - Mozilla Firefox"
        assert v.app_id == "firefox"
        assert v.name == "browser"

    def test_title_containing_equals_and_key_like_text(self):
        """Adversarial: a title that mimics the field syntax."""
        v = View.parse(
            "id=3 name=evil app_id=x "
            "title=pid=1 active=true not really "
            "active=false mapped=true pid=42"
        )
        # The anchored suffix wins: the *last* active=/mapped=/pid= triple is
        # the real one, so the fields that drive behaviour stay correct.
        assert v.id == 3
        assert v.active is False
        assert v.mapped is True
        assert v.pid == 42

    def test_negative_pid_and_id(self):
        v = View.parse(
            "id=-1 name=x app_id=y title=z active=false mapped=false pid=-1"
        )
        assert v.id == -1 and v.pid == -1

    def test_garbage_raises(self):
        with pytest.raises(ProtocolError):
            View.parse("this is not a row")

    def test_missing_trailing_field_raises(self):
        with pytest.raises(ProtocolError):
            View.parse("id=0 name=a app_id=b title=c active=true mapped=true")


class TestStatusParsing:
    def test_full_status_with_output(self):
        s = Status.parse(
            "OK active_id=0 active_name=main view_count=3 transitioning=false "
            "output=HDMI-A-1 output_width=1920 output_height=1080 refresh=60.000"
        )
        assert s.active_id == 0 and s.active_name == "main"
        assert s.view_count == 3 and s.transitioning is False
        assert s.output is not None
        assert (s.output.name, s.output.width, s.output.height) == (
            "HDMI-A-1",
            1920,
            1080,
        )
        assert s.output.refresh == pytest.approx(60.0)

    def test_status_without_output_block(self):
        """An older compositor omits the geometry fields entirely."""
        s = Status.parse(
            "OK active_id=-1 active_name=(none) view_count=0 transitioning=false"
        )
        assert s.output is None
        assert s.active_id == -1
        assert s.active_name is None

    def test_transitioning_true(self):
        s = Status.parse(
            "OK active_id=1 active_name=two view_count=2 transitioning=true "
            "output=HEADLESS-1 output_width=1280 output_height=720 refresh=0.000"
        )
        assert s.transitioning is True
        assert s.output.refresh == pytest.approx(0.0)

    def test_active_name_with_spaces(self):
        s = Status.parse(
            "OK active_id=1 active_name=my view view_count=2 transitioning=false "
            "output=HDMI-A-1 output_width=1920 output_height=1080 refresh=59.940"
        )
        assert s.active_name == "my view"
        assert s.output.refresh == pytest.approx(59.94)

    def test_garbage_raises(self):
        with pytest.raises(ProtocolError):
            Status.parse("OK nonsense")
