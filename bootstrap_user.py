import argparse

from app import create_app
from app.extensions import db
from app.models import User, Role
from app.rbac import bootstrap_rbac


def ensure_user(email: str, password: str, role_code: str, nom: str, secteur: str | None):
    """
    Crée ou met à jour un utilisateur, reset le mot de passe,
    et rattache le rôle RBAC role_code.
    """
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("Email vide")

    u = User.query.filter_by(email=email).first()
    created = False

    if not u:
        u = User(email=email, nom=nom or "Utilisateur")
        created = True

    # Legacy role (compat)
    # IMPORTANT : ton app utilise parfois 'directrice', mais RBAC a 'direction'.
    # On garde role_code pour RBAC, et on met un legacy cohérent.
    if role_code == "direction":
        u.role = "directrice"
    else:
        u.role = role_code

    if secteur is not None:
        u.secteur_assigne = secteur

    u.set_password(password)

    db.session.add(u)
    db.session.commit()

    # Rôle RBAC
    role = Role.query.filter_by(code=role_code).first()
    if not role:
        # Si jamais le rôle n’existe pas (RBAC pas bootstrappé), on le crée à minima
        role = Role(code=role_code, label=role_code)
        db.session.add(role)
        db.session.commit()

    if hasattr(u, "roles"):
        if role not in u.roles:
            u.roles.append(role)
            db.session.commit()

    return u, created


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default="admin@test.local")
    parser.add_argument("--password", default="admin1234")
    parser.add_argument("--role", default="direction", choices=["direction", "finance", "responsable_secteur", "admin_tech"])
    parser.add_argument("--nom", default="Admin Test")
    parser.add_argument("--secteur", default=None)
    args = parser.parse_args()

    app = create_app()

    with app.app_context():
        # 1) S’assure que toutes les tables existent (dont user_roles/role_permissions)
        db.create_all()

        # 2) Bootstrap RBAC (permissions + rôles + sync legacy -> RBAC)
        bootstrap_rbac()

        # 3) Crée ou répare l’utilisateur
        u, created = ensure_user(
            email=args.email,
            password=args.password,
            role_code=args.role,
            nom=args.nom,
            secteur=args.secteur,
        )

        print("=== BOOTSTRAP OK ===")
        print("created =", created)
        print("email   =", u.email)
        print("legacy  =", u.role)
        print("rbac    =", getattr(u, "role_codes", None)() if hasattr(u, "role_codes") else "n/a")
        print("password reset done.")


if __name__ == "__main__":
    main()
