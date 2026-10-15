from __future__ import annotations

from app.policy.engine import (
    ModuleView,
    Principal,
    can_browse_module,
    can_delete,
    can_delete_module,
    can_delete_module_version,
    can_delete_requirement,
    can_edit_module_content,
    can_edit_requirements,
    can_log_hours,
    can_manage_users,
    can_mutate_entity,
    can_protect,
)


def _p(role: str, grants=(), products=("usp",)) -> Principal:
    return Principal(username="u", role=role, products=frozenset(products), grants=frozenset(grants))


ENC = ModuleView("secret_mod", "usp", True, False, None, "feature")
PUB = ModuleView("biz", "usp", False, False, None, "feature")
PROT = ModuleView("sv_plan", "usp", False, True, None, "feature")


def test_encrypted_hidden_without_grant():
    p = _p("engineer")
    assert can_browse_module(p, ENC) is False
    assert can_browse_module(p, PUB) is True


def test_module_write_can_browse_encrypted():
    p = _p("engineer", grants=(("usp", "module", "secret_mod"),))
    assert can_browse_module(p, ENC) is True
    assert can_edit_module_content(p, ENC) is True


def test_admin_sees_all():
    p = _p("admin")
    assert can_browse_module(p, ENC) is True


def test_guest_no_hours():
    assert can_log_hours(_p("guest"), "usp") is False
    assert can_log_hours(_p("engineer"), "usp") is True


def test_pm_cannot_edit_feature_doc():
    p = _p("pm", grants=(("usp", "module", "biz"),))
    assert can_edit_module_content(p, PUB) is False


def test_protected_entity():
    eng = _p("engineer", grants=(("usp", "module", "sv_plan"),))
    admin = _p("admin")
    assert can_mutate_entity(eng, PROT, entity_protected=True) is True
    assert can_delete_module(eng, PROT) is False
    assert can_delete_module(admin, PROT) is False
    assert can_protect(admin) is True
    assert can_protect(eng) is False
    assert can_manage_users(admin) is True
    assert can_manage_users(eng) is False
    assert can_manage_users(_p("supervisor")) is False
    assert can_delete_module_version(eng, PUB, version_protected=True) is False
    assert can_delete_module_version(eng, PUB, version_protected=False) is False
    assert can_delete_module_version(_p("engineer", grants=(("usp", "module", "biz"),)), PUB, version_protected=False) is True


def test_supervisor_edits_all_can_delete_unprotected():
    p = _p("supervisor")
    assert can_browse_module(p, ENC) is True
    assert can_edit_module_content(p, PUB) is True
    assert can_edit_module_content(p, ENC) is True
    assert can_delete(p) is True
    assert can_delete_module(p, PUB) is True
    assert can_delete_module(p, PROT) is False
    assert can_log_hours(p, "usp") is True
    assert can_delete(_p("engineer")) is True
    assert can_delete(_p("admin")) is True
    assert can_edit_requirements(_p("supervisor")) is True
    assert can_delete_requirement(_p("supervisor"), "u") is True
    assert can_delete_requirement(_p("supervisor"), "other") is False
    assert can_delete_requirement(_p("admin"), "other") is True
    assert can_delete_requirement(_p("engineer"), "u") is True
    assert can_delete_requirement(_p("engineer"), "other") is False
    assert can_delete_requirement(_p("pm"), "u") is True
