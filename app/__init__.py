import os
from flask import Flask, url_for
from werkzeug.routing import BuildError
from sqlalchemy import text, inspect
from sqlalchemy.exc import OperationalError, ProgrammingError

from config import Config
from app.extensions import db, login_manager, csrf
from app.models import User


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    login_manager.login_view = "auth.login"

    # ---------------------------------------------------------------------
    # Jinja helper: safe_url_for
    # ---------------------------------------------------------------------
    def safe_url_for(endpoint: str, fallback: str = "#", **values) -> str:
        try:
            return url_for(endpoint, **values)
        except BuildError:
            return fallback

    app.jinja_env.globals["safe_url_for"] = safe_url_for

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # ---------------------------------------------------------------------
    # Blueprints
    # ---------------------------------------------------------------------
    from app.auth.routes import bp as auth_bp
    from app.main.routes import bp as main_bp
    from app.budget.routes import bp as budget_bp
    from app.projets.routes import bp as projets_bp
    from app.admin.routes import bp as admin_bp
    from app.activite import bp as activite_bp
    from app.kiosk import bp as kiosk_bp
    from app.statsimpact.routes import bp as statsimpact_bp
    from app.bilans.routes import bp as bilans_bp
    from app.inventaire.routes import bp as inventaire_bp
    from app.inventaire_materiel.routes import bp as inventaire_materiel_bp
    from app.participants.routes import bp as participants_bp
    from app.launcher import bp as launcher_bp
    from app.pedagogie.routes import bp as pedagogie_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(budget_bp)
    app.register_blueprint(projets_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(activite_bp)
    app.register_blueprint(kiosk_bp)
    app.register_blueprint(statsimpact_bp)
    app.register_blueprint(bilans_bp)
    app.register_blueprint(inventaire_bp)
    app.register_blueprint(inventaire_materiel_bp)
    app.register_blueprint(participants_bp)
    app.register_blueprint(launcher_bp)
    app.register_blueprint(pedagogie_bp)

    # ---------------------------------------------------------------
    # RBAC : helper Jinja (can) + bootstrap roles/perms
    # ---------------------------------------------------------------
    from app.rbac import bootstrap_rbac, can, has_role, has_any_role

    @app.context_processor
    def _inject_rbac_helpers():
        # Utilisation dans les templates:
        #   {% if can('subventions:edit') %} ... {% endif %}
        return {"can": can, "has_role": has_role, "has_any_role": has_any_role}

    # ---------------------------------------------------------------------
    # ensure_schema: migrations "légères" compatibles SQLite/Postgres
    # ---------------------------------------------------------------------
    def ensure_schema():
        """
        Migration légère *sans Alembic*.
        Objectif : garder la compatibilité SQLite **et** permettre Postgres.
        On ajoute seulement ce qui manque (colonnes / indexes) avec des ALTER/CREATE IF NOT EXISTS.
        """
        dialect = db.engine.dialect.name
        insp = inspect(db.engine)  # ✅ un seul inspector

        def has_table(name: str) -> bool:
            try:
                return insp.has_table(name)
            except Exception:
                return False

        def get_cols(table: str):
            if not has_table(table):
                return set()
            try:
                return {c["name"] for c in insp.get_columns(table)}
            except Exception:
                return set()

        def exec_sql(sql: str):
            db.session.execute(text(sql))

        def add_col(table: str, col: str, sql_sqlite: str, sql_pg: str):
            cols = get_cols(table)
            if col in cols:
                return
            if dialect == "sqlite":
                exec_sql(sql_sqlite)
            else:
                exec_sql(sql_pg)

        def create_index(sql_sqlite: str, sql_pg: str):
            if dialect == "sqlite":
                exec_sql(sql_sqlite)
            else:
                exec_sql(sql_pg)

        # 1) Finance : colonne nature sur ligne_budget
        try:
            add_col(
                "ligne_budget",
                "nature",
                "ALTER TABLE ligne_budget ADD COLUMN nature VARCHAR(10) NOT NULL DEFAULT 'charge'",
                "ALTER TABLE ligne_budget ADD COLUMN IF NOT EXISTS nature VARCHAR(10) NOT NULL DEFAULT 'charge'",
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        # 2) Activité : colonnes kiosque + soft-delete sur session_activite
        try:
            add_col(
                "session_activite",
                "is_deleted",
                "ALTER TABLE session_activite ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT 0",
                "ALTER TABLE session_activite ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE",
            )
            add_col(
                "session_activite",
                "deleted_at",
                "ALTER TABLE session_activite ADD COLUMN deleted_at DATETIME",
                "ALTER TABLE session_activite ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP",
            )
            add_col(
                "session_activite",
                "kiosk_open",
                "ALTER TABLE session_activite ADD COLUMN kiosk_open BOOLEAN NOT NULL DEFAULT 0",
                "ALTER TABLE session_activite ADD COLUMN IF NOT EXISTS kiosk_open BOOLEAN NOT NULL DEFAULT FALSE",
            )
            add_col(
                "session_activite",
                "kiosk_pin",
                "ALTER TABLE session_activite ADD COLUMN kiosk_pin VARCHAR(10)",
                "ALTER TABLE session_activite ADD COLUMN IF NOT EXISTS kiosk_pin VARCHAR(10)",
            )
            add_col(
                "session_activite",
                "kiosk_token",
                "ALTER TABLE session_activite ADD COLUMN kiosk_token VARCHAR(64)",
                "ALTER TABLE session_activite ADD COLUMN IF NOT EXISTS kiosk_token VARCHAR(64)",
            )
            add_col(
                "session_activite",
                "kiosk_opened_at",
                "ALTER TABLE session_activite ADD COLUMN kiosk_opened_at DATETIME",
                "ALTER TABLE session_activite ADD COLUMN IF NOT EXISTS kiosk_opened_at TIMESTAMP",
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        # 3) Activité : soft-delete sur atelier_activite
        try:
            add_col(
                "atelier_activite",
                "is_deleted",
                "ALTER TABLE atelier_activite ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT 0",
                "ALTER TABLE atelier_activite ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE",
            )
            add_col(
                "atelier_activite",
                "deleted_at",
                "ALTER TABLE atelier_activite ADD COLUMN deleted_at DATETIME",
                "ALTER TABLE atelier_activite ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP",
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        # 4) Participant : colonnes complémentaires (kiosk + publics)
        try:
            add_col(
                "participant",
                "signature_path",
                "ALTER TABLE participant ADD COLUMN signature_path VARCHAR(255)",
                "ALTER TABLE participant ADD COLUMN IF NOT EXISTS signature_path VARCHAR(255)",
            )
            add_col(
                "participant",
                "sexe",
                "ALTER TABLE participant ADD COLUMN sexe VARCHAR(20)",
                "ALTER TABLE participant ADD COLUMN IF NOT EXISTS sexe VARCHAR(20)",
            )
            add_col(
                "participant",
                "type_public",
                "ALTER TABLE participant ADD COLUMN type_public VARCHAR(50)",
                "ALTER TABLE participant ADD COLUMN IF NOT EXISTS type_public VARCHAR(50)",
            )
            add_col(
                "participant",
                "ville",
                "ALTER TABLE participant ADD COLUMN ville VARCHAR(100)",
                "ALTER TABLE participant ADD COLUMN IF NOT EXISTS ville VARCHAR(100)",
            )
            add_col(
                "participant",
                "quartier_id",
                "ALTER TABLE participant ADD COLUMN quartier_id INTEGER",
                "ALTER TABLE participant ADD COLUMN IF NOT EXISTS quartier_id INTEGER",
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        # 5) Archive émargement : colonnes soft-delete
        try:
            add_col(
                "archive_emargement",
                "is_deleted",
                "ALTER TABLE archive_emargement ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT 0",
                "ALTER TABLE archive_emargement ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE",
            )
            add_col(
                "archive_emargement",
                "deleted_at",
                "ALTER TABLE archive_emargement ADD COLUMN deleted_at DATETIME",
                "ALTER TABLE archive_emargement ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP",
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        # 6) Index unique anti-doublons (collectif)
        try:
            create_index(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_uq_presence_session_participant ON presence_activite(session_id, participant_id)",
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_uq_presence_session_participant ON presence_activite(session_id, participant_id)",
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        # 7) Finance : dépense liée à une ligne de facture (inventaire) + AAP (charge_projet_id)
        try:
            add_col(
                "depense",
                "facture_ligne_id",
                "ALTER TABLE depense ADD COLUMN facture_ligne_id INTEGER",
                "ALTER TABLE depense ADD COLUMN IF NOT EXISTS facture_ligne_id INTEGER",
            )
            add_col(
                "depense",
                "facture_quantite",
                "ALTER TABLE depense ADD COLUMN facture_quantite INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE depense ADD COLUMN IF NOT EXISTS facture_quantite INTEGER NOT NULL DEFAULT 1",
            )
            add_col(
                "depense",
                "charge_projet_id",
                "ALTER TABLE depense ADD COLUMN charge_projet_id INTEGER",
                "ALTER TABLE depense ADD COLUMN IF NOT EXISTS charge_projet_id INTEGER",
            )

            # Postgres : permettre des dépenses rattachées à une charge projet (ligne_budget_id nullable)
            if dialect == "postgresql":
                try:
                    db.session.execute(
                        text("ALTER TABLE depense ALTER COLUMN ligne_budget_id DROP NOT NULL")
                    )
                except Exception:
                    pass

            db.session.commit()
        except (OperationalError, ProgrammingError):
            db.session.rollback()
        except Exception:
            db.session.rollback()

    # ---------------------------------------------------------------------
    # Init DB (ordre FIXÉ pour Postgres)
    # ---------------------------------------------------------------------
    with app.app_context():
        # ✅ D'abord créer les tables
        db.create_all()

        # ✅ Puis appliquer les ALTER/INDEX "si manque"
        ensure_schema()

        # ✅ RBAC: créer tables/roles/perms & raccrocher les users existants
        bootstrap_rbac()

        # DEBUG (tu peux laisser 2 lancements, puis enlever)
        print("DB URI =", db.engine.url)
        print("DB DIALECT =", db.engine.dialect.name)

    return app
