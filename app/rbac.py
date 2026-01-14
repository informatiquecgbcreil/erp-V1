from __future__ import annotations

from functools import wraps
from typing import Iterable

from flask import abort, current_app
from flask_login import current_user

from app.extensions import db
from app.models import User, Role, Permission


# ---------------------------------------------------------------------------
# Permissions canoniques de l'application
# ---------------------------------------------------------------------------

DEFAULT_PERMS: list[tuple[str, str]] = [
    ("dashboard:view", "Voir le dashboard"),

    ("subventions:view", "Voir les subventions"),
    ("subventions:edit", "Créer/éditer les subventions"),
    ("subventions:delete", "Supprimer les subventions"),

    ("depenses:view", "Voir les dépenses"),
    ("depenses:create", "Créer des dépenses"),
    ("depenses:delete", "Supprimer les dépenses"),

    ("budget:delete", "Supprimer une ligne budgétaire"),

    ("projets:view", "Voir les projets"),
    ("projets:edit", "Créer/éditer les projets"),
    ("projets:delete", "Supprimer les projets"),

    ("participants:view", "Voir les participants"),
    ("participants:edit", "Créer/éditer les participants"),
    ("participants:delete", "Supprimer les participants"),

    ("inventaire:view", "Voir l'inventaire"),
    ("inventaire:edit", "Créer/éditer l'inventaire"),

    ("emargement:view", "Voir l'émargement"),

    ("pedagogie:view", "Voir la pédagogie"),

    ("stats:view", "Voir les stats (secteur)"),
    ("stats:view_all", "Voir les stats (tous secteurs)"),

    ("statsimpact:view", "Voir les données ateliers (secteur)"),
    ("statsimpact:view_all", "Voir les données ateliers (tous secteurs)"),

    ("controle:view", "Accéder au contrôle"),

    ("bilans:view", "Voir les bilans"),

    ("activite:delete", "Supprimer une activité"),
    ("activite:purge", "Purger des activités"),

    ("ateliers:view", "Voir les ateliers"),
    ("ateliers:sync", "Synchroniser les ateliers"),

    ("admin:users", "Gérer les utilisateurs"),
    ("admin:rbac", "Gérer les droits (RBAC)"),
]


# ---------------------------------------------------------------------------
# Modèles de rôles RBAC
# ---------------------------------------------------------------------------

ROLE_TEMPLATES: dict[str, dict[str, Iterable[str]]] = {
    "admin_tech": {
        "perms": [
            "dashboard:view",
            "admin:users",
            "admin:rbac",
        ],
    },
    "direction": {
        "perms": [code for (code, _) in DEFAULT_PERMS],
    },
    "finance": {
        "perms": [
            "dashboard:view",

            "subventions:view",
            "subventions:edit",
            "subventions:delete",

            "depenses:view",
            "depenses:create",
            "depenses:delete",

            "projets:view",
            "projets:edit",
            "projets:delete",

            "participants:view",
            "inventaire:view",
            "emargement:view",

            "stats:view",
            "bilans:view",

            "ateliers:view",
            "ateliers:sync",
        ],
    },
    "responsable_secteur": {
        "perms": [
            "dashboard:view",

            "subventions:view",

            "depenses:view",
            "depenses:create",

            "projets:view",
            "projets:edit",

            "participants:view",
            "participants:edit",

            "inventaire:view",
            "emargement:view",

            "pedagogie:view",
            "stats:view",
            "bilans:view",

            "ateliers:view",
            "ateliers:sync",
        ],
    },
}


# ---------------------------------------------------------------------------
# Outils internes
# ---------------------------------------------------------------------------

def _category_from_code(code: str) -> str:
    module = (code.split(":", 1)[0] if ":" in code else code).strip()
    mapping = {
        "dashboard": "Dashboard",
        "subventions": "Subventions",
        "depenses": "Dépenses",
        "budget": "Budget",
        "projets": "Projets",
        "participants": "Participants",
        "inventaire": "Inventaire",
        "emargement": "Émargement",
        "pedagogie": "Pédagogie",
        "stats": "Stats",
        "statsimpact": "Stats impact",
        "bilans": "Bilans",
        "ateliers": "Ateliers",
        "admin": "Admin",
    }
    return mapping.get(module, module.capitalize())


# ---------------------------------------------------------------------------
# Bootstrap RBAC (TOUT est ici, jamais au niveau global)
# ---------------------------------------------------------------------------

def bootstrap_rbac() -> None:
    try:
        db.create_all()
    except Exception:
        current_app.logger.exception("RBAC: db.create_all() a échoué")
        return

    # --- Permissions ---
    existing = {p.code: p for p in Permission.query.all()}
    changed = False

    for code, label in DEFAULT_PERMS:
        if code not in existing:
            db.session.add(
                Permission(
                    code=code,
                    label=label,
                    category=_category_from_code(code),
                )
            )
            changed = True
        else:
            p = existing[code]
            if p.label != label:
                p.label = label
                changed = True
            new_cat = _category_from_code(code)
            if getattr(p, "category", None) != new_cat:
                p.category = new_cat
                changed = True

    if changed:
        db.session.commit()

    perms_by_code = {p.code: p for p in Permission.query.all()}

    # --- Rôles ---
    for role_code, cfg in ROLE_TEMPLATES.items():
        role = Role.query.filter_by(code=role_code).first()
        if not role:
            role = Role(code=role_code, label=role_code)
            db.session.add(role)
            db.session.flush()

        wanted = set(cfg.get("perms", []))
        role.permissions = [
            perms_by_code[c] for c in wanted if c in perms_by_code
        ]

    db.session.commit()

    # --- Synchronisation legacy User.role -> RBAC ---
    legacy_map = {
        "directrice": "direction",
        "direction": "direction",
        "financiere": "finance",
        "financière": "finance",
        "finance": "finance",
        "responsable_secteur": "responsable_secteur",
        "admin_tech": "admin_tech",
    }

    users = User.query.all()
    for u in users:
        if not hasattr(u, "roles"):
            continue

        legacy = (u.role or "responsable_secteur").strip()
        target = legacy_map.get(legacy, legacy)

        role = Role.query.filter_by(code=target).first()
        if role and role not in u.roles:
            u.roles.append(role)

    db.session.commit()


# ---------------------------------------------------------------------------
# Équivalences de permissions (tolérance historique)
# ---------------------------------------------------------------------------

PERM_EQUIVALENTS: dict[str, set[str]] = {
    "statsimpact:view": {"statsimpact:view", "stats:view"},
    "bilan:view": {"bilan:view", "bilans:view"},
    "bilans:lourds:view": {"bilans:lourds:view", "bilans:view"},
    "participants:update": {"participants:update", "participants:edit"},
    "participants:write": {"participants:write", "participants:edit"},
    "participant:edit": {"participant:edit", "participants:edit"},
    "projets_edit": {"projets_edit", "projets:edit"},
}


def _expand_perm(code: str) -> set[str]:
    code = (code or "").strip()
    if not code:
        return set()
    if code in PERM_EQUIVALENTS:
        return set(PERM_EQUIVALENTS[code])
    return {code}


# ---------------------------------------------------------------------------
# Décorateurs / helpers
# ---------------------------------------------------------------------------

def require_perm(code: str):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)

            has_perm_fn = getattr(current_user, "has_perm", None)
            if not callable(has_perm_fn):
                abort(403)

            wanted = _expand_perm(code)
            if not wanted:
                abort(403)

            if not any(has_perm_fn(c) for c in wanted):
                abort(403)

            return fn(*args, **kwargs)

        return wrapper
    return decorator


def can(code: str) -> bool:
    if not current_user.is_authenticated:
        return False

    has_perm_fn = getattr(current_user, "has_perm", None)
    if not callable(has_perm_fn):
        return False

    wanted = _expand_perm(code)
    if not wanted:
        return False

    return any(has_perm_fn(c) for c in wanted)


def has_role(code: str) -> bool:
    if not current_user.is_authenticated:
        return False

    has_role_fn = getattr(current_user, "has_role", None)
    if callable(has_role_fn):
        return has_role_fn(code)

    return code in getattr(current_user, "role_codes", [])


def has_any_role(codes: Iterable[str]) -> bool:
    if not current_user.is_authenticated:
        return False

    has_any_role_fn = getattr(current_user, "has_any_role", None)
    if callable(has_any_role_fn):
        return has_any_role_fn(codes)

    role_codes = set(getattr(current_user, "role_codes", []))
    return any(code in role_codes for code in codes)
