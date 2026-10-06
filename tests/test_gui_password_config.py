"""load_gui_password_config: no generated/"bootstrap" password exists any more
(the field was always None and a startup banner printed it if it ever wasn't --
a password in a log file is exactly what must not exist)."""

from gui.gui_security import load_gui_password_config


def test_config_has_no_bootstrap_password_field():
    for env in ({}, {"PRISM_GUI_PASSWORD": "pw"}, {"PRISM_GUI_PASSWORD_HASH": "h"}, {"PRISM_GUI_DISABLE_LOGIN": "1"}):
        import os

        old = {k: os.environ.pop(k, None) for k in ("PRISM_GUI_PASSWORD", "PRISM_GUI_PASSWORD_HASH", "PRISM_GUI_DISABLE_LOGIN")}
        try:
            os.environ.update(env)
            assert set(load_gui_password_config()) == {"enabled", "password_hash", "source"}
        finally:
            for k in env:
                os.environ.pop(k, None)
            os.environ.update({k: v for k, v in old.items() if v is not None})
