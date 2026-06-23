from flask import Flask, flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from services.forecasting import (
    choose_best_model,
    exp_smoothing,
    holt_from_excel,
    linear_regression_trend,
    moving_average,
    seasonal_simple,
    seasonal_trend,
)
from extensions import db
from models import Demanda, Projeto, Usuario

from sqlalchemy import inspect, text
from sqlalchemy.engine import URL

import io
import json
import math
import os
import unicodedata
import xml.etree.ElementTree as ET
import zipfile


ADMIN_LOGIN = "admin"
ADMIN_PASSWORD = "china0000"
schema_checked = False
XLSX_NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _palette(idx):
    colors = ['#66d9ef', '#f78c6c', '#ffd866', '#c3e88d', '#82aaff', '#ff9cac', '#f2c2ff', '#7ae7c7']
    return colors[idx % len(colors)]


def _database_uri():
    database_url = (
        os.getenv("DATABASE_URL")
        or os.getenv("DATABASE_PRIVATE_URL")
        or os.getenv("DATABASE_PUBLIC_URL")
        or os.getenv("POSTGRES_URL")
        or os.getenv("POSTGRES_PRIVATE_URL")
    )
    if database_url:
        if database_url.startswith("postgres://"):
            database_url = "postgresql+pg8000://" + database_url[len("postgres://"):]
        elif database_url.startswith("postgresql://"):
            database_url = "postgresql+pg8000://" + database_url[len("postgresql://"):]
        return database_url

    railway_env = os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID")
    pg_host = os.getenv("POSTGRES_HOST") or os.getenv("PGHOST")
    pg_user = os.getenv("POSTGRES_USER") or os.getenv("PGUSER")
    pg_password = os.getenv("POSTGRES_PASSWORD") or os.getenv("PGPASSWORD")
    pg_database = os.getenv("POSTGRES_DB") or os.getenv("PGDATABASE")
    pg_port = os.getenv("POSTGRES_PORT") or os.getenv("PGPORT")

    if railway_env and not any([pg_host, pg_user, pg_password, pg_database, pg_port]):
        raise RuntimeError(
            "Variáveis do PostgreSQL não foram configuradas no serviço web do Railway. "
            "Defina DATABASE_URL=${{Postgres.DATABASE_URL}} ou vincule PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE."
        )

    return str(
        URL.create(
            "postgresql+pg8000",
            username=pg_user or "postgres",
            password=pg_password or "pepito1",
            host=pg_host or "localhost",
            port=int(pg_port or "5432"),
            database=pg_database or "SiteJP",
        )
    )


def _safe_num(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n:
        return None
    return n


def _ceil_num(v):
    n = _safe_num(v)
    if n is None:
        return None
    integer = math.trunc(n)
    if abs(n - integer) < 0.05:
        return int(integer)
    return int(math.floor(n) if n < 0 else math.ceil(n))


def _ensure_schema():
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

    required_tables = {"usuarios", "projetos", "demandas"}
    if not required_tables.issubset(existing_tables):
        db.create_all()
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())

    user_columns = {column["name"] for column in inspector.get_columns("usuarios")}
    if "aprovado" not in user_columns:
        db.session.execute(text("ALTER TABLE usuarios ADD COLUMN aprovado BOOLEAN NOT NULL DEFAULT FALSE"))

    project_columns = {column["name"] for column in inspector.get_columns("projetos")}
    if "responsavel" not in project_columns:
        db.session.execute(text("ALTER TABLE projetos ADD COLUMN responsavel VARCHAR(120) NULL"))

    db.session.commit()


def _is_user_logged():
    return "user_id" in session


def _is_admin_logged():
    return bool(session.get("is_admin"))


def _require_user():
    if not _is_user_logged():
        return redirect(url_for("login"))
    return None


def _require_admin():
    if not _is_admin_logged():
        return redirect(url_for("admin_login"))
    return None


def _serialize_results(results):
    return [{
        "name": r.name,
        "params": r.params,
        "mad": r.mad,
        "next_forecast": r.next_forecast,
        "forecast": r.forecast,
        "error": r.error,
        "abs_error": r.abs_error,
    } for r in results]


def _display_method_name(name):
    labels = {
        "media_movel_3": "Média Móvel (3 períodos)",
        "media_movel_6": "Média Móvel (6 períodos)",
        "media_movel_12": "Média Móvel (12 períodos)",
        "media_exponencial_0.10": "Média Exponencial Móvel (a = 0,10)",
        "media_exponencial_0.50": "Média Exponencial Móvel (a = 0,50)",
        "media_exponencial_0.80": "Média Exponencial Móvel (a = 0,80)",
        "ajuste_tendencia_holt": "Ajustamento Exponencial com Tendência (Holt)",
        "equacao_linear": "Equação Linear",
    }
    if name and name.startswith("sazonalidade_simples_"):
        periodos = name.rsplit("_", 1)[-1]
        return f"Sazonalidade Simples ({periodos} períodos)"
    if name and name.startswith("sazonalidade_tendencia_"):
        periodos = name.rsplit("_", 1)[-1]
        return f"Sazonalidade com Tendência ({periodos} períodos)"
    return labels.get(name, name or "-")


def _seasonal_lengths(total_periods):
    return [length for length in (6, 12) if total_periods >= length]


def _run_forecasting_models(demandas_int):
    seasonal_lengths = _seasonal_lengths(len(demandas_int))
    results = [
        moving_average(demandas_int, 3),
        moving_average(demandas_int, 6),
        moving_average(demandas_int, 12),
        *[seasonal_simple(demandas_int, length) for length in seasonal_lengths],
        exp_smoothing(demandas_int, 0.10),
        exp_smoothing(demandas_int, 0.50),
        exp_smoothing(demandas_int, 0.80),
        holt_from_excel(demandas_int, 0.70, 0.30),
        linear_regression_trend(demandas_int),
        *[seasonal_trend(demandas_int, length) for length in seasonal_lengths],
    ]
    eval_start = 13 if len(demandas_int) >= 13 else 2
    return choose_best_model(results, eval_start_period=eval_start)


def _xlsx_shared_strings(zf):
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []

    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings = []
    for si in root.findall("main:si", XLSX_NS):
        parts = []
        for node in si.iterfind(".//main:t", XLSX_NS):
            parts.append(node.text or "")
        strings.append("".join(parts))
    return strings


def _xlsx_cell_value(cell, shared_strings):
    value_node = cell.find("main:v", XLSX_NS)
    if value_node is None:
        return None

    value = value_node.text
    cell_type = cell.attrib.get("t")
    if cell_type == "s" and value is not None:
        return shared_strings[int(value)]
    return value


def _normalize_header(value):
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return ascii_value.strip().lower().replace(".", "").replace(" ", "")


def _parse_excel_demands(file_storage):
    raw = file_storage.read()
    if not raw:
        raise ValueError("Arquivo vazio.")

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        workbook_xml = ET.fromstring(zf.read("xl/workbook.xml"))
        sheets = workbook_xml.find("main:sheets", XLSX_NS)
        if sheets is None or not list(sheets):
            raise ValueError("Planilha sem abas válidas.")

        first_sheet_path = "xl/worksheets/sheet1.xml"
        if first_sheet_path not in zf.namelist():
            raise ValueError("Não foi possível ler a primeira aba.")

        shared_strings = _xlsx_shared_strings(zf)
        sheet_xml = ET.fromstring(zf.read(first_sheet_path))
        sheet_data = sheet_xml.find("main:sheetData", XLSX_NS)
        if sheet_data is None:
            raise ValueError("Planilha sem dados.")

        rows = []
        for row in sheet_data.findall("main:row", XLSX_NS):
            values = []
            for cell in row.findall("main:c", XLSX_NS):
                values.append(_xlsx_cell_value(cell, shared_strings))
            if any(value not in (None, "") for value in values):
                rows.append(values)

    header_idx = None
    for idx, row in enumerate(rows):
        normalized = [_normalize_header(str(value or "")) for value in row]
        if "periodo" in normalized and "dreal" in normalized:
            header_idx = idx
            break

    if header_idx is None:
        raise ValueError("Use este formato: Período e D.Real.")

    header = [_normalize_header(str(value or "")) for value in rows[header_idx]]
    try:
        demand_col = header.index("dreal")
    except ValueError as exc:
        raise ValueError("Coluna D.Real não encontrada.") from exc

    demandas = []
    for row in rows[header_idx + 1:]:
        if demand_col >= len(row):
            continue
        raw_value = row[demand_col]
        if raw_value in (None, ""):
            continue
        try:
            demandas.append(int(round(float(str(raw_value).replace(",", ".")))))
        except ValueError as exc:
            raise ValueError("A coluna D.Real precisa ter apenas números.") from exc

    if not demandas:
        raise ValueError("Nenhuma demanda válida foi encontrada no Excel.")
    return demandas


def build_line_chart(demandas_vals, detalhes, melhor_metodo):
    width = 900
    height = 230
    pad = {"top": 14, "right": 14, "bottom": 22, "left": 34}
    inner_w = width - pad["left"] - pad["right"]
    inner_h = height - pad["top"] - pad["bottom"]

    series = [{
        "name": "Demanda real",
        "values": [float(v) for v in demandas_vals],
        "color": "#ffffff",
        "dash": None,
        "highlight": True
    }]

    for idx, r in enumerate(detalhes or []):
        series.append({
            "name": r.get("name") or f"modelo_{idx + 1}",
            "values": [_safe_num(v) for v in (r.get("forecast") or [])],
            "color": _palette(idx),
            "dash": None if r.get("name") == melhor_metodo else "6 4",
            "highlight": r.get("name") == melhor_metodo
        })

    all_vals = []
    for s in series:
        for v in s["values"]:
            if v is not None:
                all_vals.append(v)

    if not all_vals:
        return {"has_data": False, "width": width, "height": height}

    vmin = min(all_vals)
    vmax = max(all_vals)
    span = max(1.0, vmax - vmin)
    max_len = max([len(s["values"]) for s in series] + [1])

    def x(i):
        return pad["left"] + (i / max(1, max_len - 1)) * inner_w

    def y(v):
        return pad["top"] + (1 - ((v - vmin) / span)) * inner_h

    out_series = []
    for s in series:
        parts = []
        for i, v in enumerate(s["values"]):
            if v is None:
                continue
            cmd = "L" if parts else "M"
            parts.append(f"{cmd} {x(i):.2f} {y(v):.2f}")
        if parts:
            out_series.append({
                "name": s["name"],
                "color": s["color"],
                "dash": s["dash"],
                "stroke_width": 2.8 if s["highlight"] else 1.8,
                "path": " ".join(parts)
            })

    return {
        "has_data": True,
        "width": width,
        "height": height,
        "x_axis": {"x1": pad["left"], "y1": height - pad["bottom"], "x2": width - pad["right"], "y2": height - pad["bottom"]},
        "y_axis": {"x1": pad["left"], "y1": pad["top"], "x2": pad["left"], "y2": height - pad["bottom"]},
        "series": out_series
    }


def build_single_model_chart(demandas_vals, forecast_vals):
    width = 900
    height = 248
    pad = {"top": 12, "right": 12, "bottom": 34, "left": 34}
    inner_w = width - pad["left"] - pad["right"]
    inner_h = height - pad["top"] - pad["bottom"]

    real = [_safe_num(v) for v in demandas_vals]
    pred = [_safe_num(v) for v in (forecast_vals or [])]
    all_vals = [v for v in real if v is not None] + [v for v in pred if v is not None]
    if not all_vals:
        return {"has_data": False, "width": width, "height": height}

    vmin = min(all_vals)
    vmax = max(all_vals)
    span = max(1.0, vmax - vmin)
    max_len = max(len(real), len(pred), 1)

    def x(i):
        return pad["left"] + (i / max(1, max_len - 1)) * inner_w

    def y(v):
        return pad["top"] + (1 - ((v - vmin) / span)) * inner_h

    def path_from(values):
        parts = []
        for i, v in enumerate(values):
            if v is None:
                continue
            cmd = "L" if parts else "M"
            parts.append(f"{cmd} {x(i):.2f} {y(v):.2f}")
        return " ".join(parts)

    y_grid = []
    for step in range(5):
        frac = step / 4
        yv = pad["top"] + (1 - frac) * inner_h
        vv = vmin + (frac * span)
        y_grid.append({"y": round(yv, 2), "label": f"{vv:.1f}"})

    def points_from(values):
        pts = []
        for i, v in enumerate(values):
            if v is None:
                continue
            pts.append({"x": round(x(i), 2), "y": round(y(v), 2), "v": round(v, 2)})
        return pts

    x_ticks = []
    stride = max(1, max_len // 6)
    for i in range(0, max_len, stride):
        x_ticks.append({"x": round(x(i), 2), "label": str(i + 1)})
    if x_ticks[-1]["label"] != str(max_len):
        x_ticks.append({"x": round(x(max_len - 1), 2), "label": str(max_len)})

    return {
        "has_data": True,
        "width": width,
        "height": height,
        "x_axis": {"x1": pad["left"], "y1": height - pad["bottom"], "x2": width - pad["right"], "y2": height - pad["bottom"]},
        "y_axis": {"x1": pad["left"], "y1": pad["top"], "x2": pad["left"], "y2": height - pad["bottom"]},
        "real_path": path_from(real),
        "pred_path": path_from(pred),
        "real_points": points_from(real),
        "pred_points": points_from(pred),
        "y_grid": y_grid,
        "x_ticks": x_ticks,
    }


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "chave-super-secreta")

app.config["SQLALCHEMY_DATABASE_URI"] = _database_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)


@app.context_processor
def inject_helpers():
    return {"display_method_name": _display_method_name, "ceil_num": _ceil_num}


@app.before_request
def bootstrap_schema():
    global schema_checked
    if schema_checked:
        return

    _ensure_schema()
    schema_checked = True


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(app.root_path, "icosite.ico", mimetype="image/x-icon")


@app.route("/assets/exemple.jpg")
def exemple_image():
    return send_from_directory(app.root_path, "exemple.jpg")


@app.route("/", methods=["GET", "POST"])
def login():
    if _is_admin_logged():
        return redirect(url_for("admin_dashboard"))
    if _is_user_logged():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form["email"].strip()
        senha = request.form["senha"]

        user = Usuario.query.filter_by(email=email, senha=senha).first()
        if not user:
            flash("Email ou senha inválidos.", "error")
            return redirect(url_for("login"))
        if not user.aprovado:
            flash("Sua conta ainda está aguardando aprovação da equipe de TI.", "warning")
            return redirect(url_for("login"))

        session.clear()
        session["user_id"] = user.id
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if _is_admin_logged():
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        login_value = (request.form.get("login") or "").strip()
        senha = request.form.get("senha") or ""

        if login_value != ADMIN_LOGIN or senha != ADMIN_PASSWORD:
            flash("Credenciais de administrador inválidas.", "error")
            return redirect(url_for("admin_login"))

        session.clear()
        session["is_admin"] = True
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_login.html")


@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    if request.method == "POST":
        email = request.form["email"].strip()
        senha = request.form["senha"]
        confirmar = request.form["confirmar"]
        empresa = (request.form["empresa"] or "").strip()

        if not empresa:
            flash("Preencha a empresa.", "error")
            return redirect(url_for("cadastro"))
        if not email or not senha:
            flash("Preencha email e senha.", "error")
            return redirect(url_for("cadastro"))
        if senha != confirmar:
            flash("As senhas não conferem.", "error")
            return redirect(url_for("cadastro"))

        existe = Usuario.query.filter_by(email=email).first()
        if existe:
            flash("Esse email ja esta cadastrado.", "error")
            return redirect(url_for("cadastro"))

        novo = Usuario(email=email, senha=senha, empresa=empresa, aprovado=False)
        db.session.add(novo)
        db.session.commit()

        flash("Cadastro enviado. A equipe de TI precisa aprovar sua conta antes do primeiro login.", "success")
        return redirect(url_for("login"))

    return render_template("cadastro.html")


@app.route("/dashboard", methods=["GET"])
def dashboard():
    redirect_response = _require_user()
    if redirect_response:
        return redirect_response

    user = Usuario.query.get(session["user_id"])
    if not user:
        session.clear()
        return redirect(url_for("login"))
    if not user.aprovado:
        session.clear()
        flash("Sua conta ainda não foi aprovada.", "warning")
        return redirect(url_for("login"))

    projetos = Projeto.query.filter_by(usuario_id=session["user_id"]).all()
    return render_template("dashboard.html", projetos=projetos, user=user, exemplo_excel_url=url_for("exemple_image"))


@app.route("/configuracoes", methods=["GET"])
def settings():
    redirect_response = _require_user()
    if redirect_response:
        return redirect_response

    user = Usuario.query.get(session["user_id"])
    if not user:
        session.clear()
        return redirect(url_for("login"))

    projetos_count = Projeto.query.filter_by(usuario_id=user.id).count()
    return render_template("settings.html", user=user, projetos_count=projetos_count)


@app.route("/admin")
def admin_dashboard():
    redirect_response = _require_admin()
    if redirect_response:
        return redirect_response

    filtro = (request.args.get("status") or "todos").strip().lower()
    termo = (request.args.get("q") or "").strip()

    query = Usuario.query.order_by(Usuario.aprovado.asc(), Usuario.empresa.asc(), Usuario.email.asc())
    if filtro == "pendentes":
        query = query.filter_by(aprovado=False)
    elif filtro == "ativos":
        query = query.filter_by(aprovado=True)
    if termo:
        like = f"%{termo}%"
        query = query.filter((Usuario.email.ilike(like)) | (Usuario.empresa.ilike(like)))

    usuarios = query.all()
    pendentes = Usuario.query.filter_by(aprovado=False).count()
    ativos = Usuario.query.filter_by(aprovado=True).count()

    return render_template(
        "admin_dashboard.html",
        usuarios=usuarios,
        pendentes=pendentes,
        ativos=ativos,
        filtro=filtro,
        termo=termo,
        total=pendentes + ativos,
    )


@app.route("/admin/usuarios/<int:user_id>/aprovar", methods=["POST"])
def admin_aprovar_usuario(user_id):
    redirect_response = _require_admin()
    if redirect_response:
        return jsonify({"ok": False, "error": "Não autorizado"}), 401

    user = Usuario.query.get(user_id)
    if not user:
        return jsonify({"ok": False, "error": "Usuário não encontrado."}), 404

    user.aprovado = True
    db.session.commit()
    return jsonify({"ok": True, "id": user.id})


@app.route("/admin/usuarios/<int:user_id>/reprovar", methods=["POST"])
def admin_reprovar_usuario(user_id):
    redirect_response = _require_admin()
    if redirect_response:
        return jsonify({"ok": False, "error": "Não autorizado"}), 401

    user = Usuario.query.get(user_id)
    if not user:
        return jsonify({"ok": False, "error": "Usuário não encontrado."}), 404

    user.aprovado = False
    db.session.commit()
    return jsonify({"ok": True, "id": user.id})


@app.route("/admin/usuarios/<int:user_id>/deletar", methods=["POST"])
def admin_deletar_usuario(user_id):
    redirect_response = _require_admin()
    if redirect_response:
        return jsonify({"ok": False, "error": "Não autorizado"}), 401

    data = request.get_json(silent=True) or {}
    confirmacao = (data.get("confirmacao") or "").strip().upper()
    if confirmacao != "DELETAR":
        return jsonify({"ok": False, "error": "Confirmação inválida. Digite DELETAR."}), 400

    user = Usuario.query.get(user_id)
    if not user:
        return jsonify({"ok": False, "error": "Usuário não encontrado."}), 404

    projetos = Projeto.query.filter_by(usuario_id=user.id).all()
    for projeto in projetos:
        Demanda.query.filter_by(projeto_id=projeto.id).delete()
        db.session.delete(projeto)

    db.session.delete(user)
    db.session.commit()
    return jsonify({"ok": True, "id": user_id})


@app.route("/importar-demandas-excel", methods=["POST"])
def importar_demandas_excel():
    if not _is_user_logged():
        return jsonify({"ok": False, "error": "Não autenticado"}), 401

    file_storage = request.files.get("arquivo")
    if not file_storage or not file_storage.filename:
        return jsonify({"ok": False, "error": "Selecione um arquivo Excel."}), 400
    if not file_storage.filename.lower().endswith(".xlsx"):
        return jsonify({"ok": False, "error": "Envie um arquivo .xlsx."}), 400

    try:
        demandas = _parse_excel_demands(file_storage)
    except zipfile.BadZipFile:
        return jsonify({"ok": False, "error": "Arquivo inválido ou corrompido."}), 400
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "demandas": demandas, "periodos": len(demandas)})


@app.route("/projeto/<int:projeto_id>")
def projeto_detalhe(projeto_id):
    redirect_response = _require_user()
    if redirect_response:
        return redirect_response

    projeto = Projeto.query.filter_by(id=projeto_id, usuario_id=session["user_id"]).first_or_404()
    demandas = Demanda.query.filter_by(projeto_id=projeto.id).order_by(Demanda.periodo.asc()).all()

    detalhes = []
    if projeto.detalhes_json:
        try:
            detalhes = projeto.detalhes_json
            if isinstance(detalhes, str):
                detalhes = json.loads(detalhes)
        except Exception:
            detalhes = []

    if demandas:
        demandas_int = [int(d.valor) for d in demandas]
        best, all_results = _run_forecasting_models(demandas_int)

        projeto.melhor_metodo = best.name
        projeto.mad = float(best.mad) if best.mad is not None else None
        projeto.previsao_prox = _ceil_num(best.next_forecast)
        detalhes = _serialize_results(all_results)
        projeto.detalhes_json = detalhes
        db.session.commit()

    demandas_vals = [int(d.valor) for d in demandas]
    model_charts = {}
    for r in detalhes:
        name = r.get("name")
        if name:
            model_charts[name] = build_single_model_chart(demandas_vals, r.get("forecast") or [])

    return render_template(
        "projeto_detalhe.html",
        projeto=projeto,
        demandas=demandas,
        detalhes=detalhes,
        model_charts=model_charts
    )


@app.route("/perfil/senha", methods=["POST"])
def perfil_senha():
    if not _is_user_logged():
        return jsonify({"ok": False, "error": "Não autenticado"}), 401

    data = request.get_json(silent=True) or {}
    atual = (data.get("senha_atual") or "").strip()
    nova = (data.get("senha_nova") or "").strip()
    confirmar = (data.get("confirmar") or "").strip()

    if not atual or not nova or not confirmar:
        return jsonify({"ok": False, "error": "Preencha todos os campos."}), 400
    if len(nova) < 4:
        return jsonify({"ok": False, "error": "A nova senha deve ter pelo menos 4 caracteres."}), 400
    if nova != confirmar:
        return jsonify({"ok": False, "error": "Confirmação não confere."}), 400

    user = Usuario.query.get(session["user_id"])
    if not user or user.senha != atual:
        return jsonify({"ok": False, "error": "Senha atual inválida."}), 400

    user.senha = nova
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/perfil/empresa", methods=["POST"])
def perfil_empresa():
    if not _is_user_logged():
        return jsonify({"ok": False, "error": "Não autenticado"}), 401

    data = request.get_json(silent=True) or {}
    empresa = (data.get("empresa") or "").strip()
    if len(empresa) < 2:
        return jsonify({"ok": False, "error": "Informe um nome de empresa válido."}), 400

    user = Usuario.query.get(session["user_id"])
    if not user:
        return jsonify({"ok": False, "error": "Usuário não encontrado."}), 404

    user.empresa = empresa
    db.session.commit()
    return jsonify({"ok": True, "empresa": user.empresa})


@app.route("/novo-projeto", methods=["POST"])
def novo_projeto():
    if not _is_user_logged():
        return jsonify({"ok": False, "error": "Não autenticado"}), 401

    data = request.get_json(silent=True) or {}
    nome = (data.get("nome") or "").strip()
    descricao = (data.get("descricao") or "").strip()
    responsavel = (data.get("responsavel") or "").strip()
    try:
        periodos = int(data.get("periodos") or 0)
    except ValueError:
        periodos = 0
    demandas = data.get("demandas") or []

    if not nome or not descricao or not responsavel or periodos < 2:
        return jsonify({"ok": False, "error": "Preencha nome, funcionário, descrição e ao menos 2 períodos."}), 400
    if len(demandas) != periodos:
        return jsonify({"ok": False, "error": "Quantidade de demandas não bate com períodos."}), 400

    demandas_int = []
    for i, valor in enumerate(demandas, start=1):
        try:
            v = int(valor)
        except ValueError:
            return jsonify({"ok": False, "error": f"Demanda inválida no período {i}."}), 400
        demandas_int.append(v)

    projeto = Projeto(
        nome=nome,
        descricao=descricao,
        responsavel=responsavel,
        usuario_id=session["user_id"],
        periodos=periodos
    )
    db.session.add(projeto)
    db.session.flush()

    for i, v in enumerate(demandas_int, start=1):
        db.session.add(Demanda(projeto_id=projeto.id, periodo=i, valor=v))

    best, all_results = _run_forecasting_models(demandas_int)

    projeto.melhor_metodo = best.name
    projeto.mad = float(best.mad) if best.mad is not None else None
    projeto.previsao_prox = _ceil_num(best.next_forecast)
    projeto.detalhes_json = _serialize_results(all_results)
    db.session.commit()

    return jsonify({
        "ok": True,
        "projeto": {
            "id": projeto.id,
            "nome": projeto.nome,
            "descricao": projeto.descricao,
            "responsavel": projeto.responsavel,
            "periodos": projeto.periodos,
            "melhor_modelo": projeto.melhor_metodo,
            "melhor_modelo_label": _display_method_name(projeto.melhor_metodo),
            "mad": projeto.mad,
            "previsao_prox": projeto.previsao_prox
        }
    })


@app.route("/projeto/<int:projeto_id>/deletar", methods=["POST"])
def deletar_projeto(projeto_id):
    if not _is_user_logged():
        return jsonify({"ok": False, "error": "Não autenticado"}), 401

    projeto = Projeto.query.filter_by(id=projeto_id, usuario_id=session["user_id"]).first()
    if not projeto:
        return jsonify({"ok": False, "error": "Projeto não encontrado."}), 404

    data = request.get_json(silent=True) or {}
    confirmacao = (data.get("confirmacao") or "").strip().upper()
    if confirmacao != "DELETAR":
        return jsonify({"ok": False, "error": "Confirmação inválida. Digite DELETAR."}), 400

    Demanda.query.filter_by(projeto_id=projeto.id).delete()
    db.session.delete(projeto)
    db.session.commit()
    return jsonify({"ok": True, "id": projeto_id})


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(debug=True)
