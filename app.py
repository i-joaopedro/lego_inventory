import os
import secrets
import qrcode
import logging
from io import BytesIO
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, send_file, session, abort, jsonify)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                 Paragraph, Spacer, HRFlowable,
                                 Image as RLImage)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm

# ─────────────────────────────────────────────
#  CONFIGURAÇÃO
# ─────────────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(32))
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///lego_pro.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
app.config['WTF_CSRF_ENABLED'] = True

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s',
                    handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  MODELOS
# ─────────────────────────────────────────────
class Usuario(db.Model):
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    escola = db.Column(db.String(200), nullable=True)
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    criado_em = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    ultimo_login = db.Column(db.DateTime, nullable=True)

    def set_password(self, raw): self.password = generate_password_hash(raw)
    def check_password(self, raw): return check_password_hash(self.password, raw)


class Peca(db.Model):
    __tablename__ = 'pecas'
    id = db.Column(db.Integer, primary_key=True)
    codigo_lego = db.Column(db.String(50), unique=True, nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    imagem_url = db.Column(db.String(255), default='sem-foto.png')
    composicoes = db.relationship('ComposicaoKit', backref='peca',
                                   cascade='all, delete-orphan', lazy=True)


class KitModelo(db.Model):
    __tablename__ = 'kit_modelos'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    categoria = db.Column(db.String(50))
    foto_capa = db.Column(db.String(255), default='kit-default.png')
    pecas_obrigatorias = db.relationship('ComposicaoKit', backref='kit_modelo',
                                          cascade='all, delete-orphan', lazy=True)
    unidades_reais = db.relationship('KitUnidade', backref='modelo',
                                      cascade='all, delete-orphan', lazy=True)


class ComposicaoKit(db.Model):
    __tablename__ = 'composicao_kits'
    id = db.Column(db.Integer, primary_key=True)
    kit_modelo_id = db.Column(db.Integer, db.ForeignKey('kit_modelos.id'))
    peca_id = db.Column(db.Integer, db.ForeignKey('pecas.id'))
    quantidade_esperada = db.Column(db.Integer, nullable=False, default=1)


class KitUnidade(db.Model):
    __tablename__ = 'kit_unidades'
    id = db.Column(db.Integer, primary_key=True)
    identificador = db.Column(db.String(50), nullable=False)
    kit_modelo_id = db.Column(db.Integer, db.ForeignKey('kit_modelos.id'))
    escola = db.Column(db.String(100), default='Laboratório Central')
    status_atual = db.Column(db.String(20), default='Pendente')
    conferencias = db.relationship('Conferencia', backref='unidade',
                                    cascade='all, delete-orphan', lazy=True)

    @property
    def ultima_conferencia(self):
        if self.conferencias:
            return max(self.conferencias, key=lambda c: c.data_conferencia)
        return None

    @property
    def saude_percentual(self):
        uc = self.ultima_conferencia
        if not uc or not uc.detalhes: return None
        esp = sum(d.quantidade_esperada_na_epoca for d in uc.detalhes)
        enc = sum(d.quantidade_encontrada for d in uc.detalhes)
        return round(enc / esp * 100, 1) if esp else 100.0


class Conferencia(db.Model):
    __tablename__ = 'conferencias'
    id = db.Column(db.Integer, primary_key=True)
    kit_unidade_id = db.Column(db.Integer, db.ForeignKey('kit_unidades.id'))
    data_conferencia = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    responsavel = db.Column(db.String(100))
    observacoes = db.Column(db.Text)
    status_resultado = db.Column(db.String(20))
    detalhes = db.relationship('ConferenciaDetalhe', backref='conferencia',
                                cascade='all, delete-orphan', lazy=True)


class ConferenciaDetalhe(db.Model):
    __tablename__ = 'conferencia_detalhes'
    id = db.Column(db.Integer, primary_key=True)
    conferencia_id = db.Column(db.Integer, db.ForeignKey('conferencias.id'))
    peca_id = db.Column(db.Integer, db.ForeignKey('pecas.id'))
    quantidade_esperada_na_epoca = db.Column(db.Integer)
    quantidade_encontrada = db.Column(db.Integer, nullable=False)
    observacao_peca = db.Column(db.String(200), nullable=True)
    peca = db.relationship('Peca')


class APIToken(db.Model):
    __tablename__ = 'api_tokens'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False)
    escola = db.Column(db.String(200), nullable=True)
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    ultimo_uso = db.Column(db.DateTime, nullable=True)


class Escola(db.Model):
    __tablename__ = 'escolas'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)
    cidade = db.Column(db.String(100))
    responsavel = db.Column(db.String(100))
    telefone = db.Column(db.String(30))
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def kits(self): return KitUnidade.query.filter_by(escola=self.nome).all()
    @property
    def total_kits(self): return KitUnidade.query.filter_by(escola=self.nome).count()
    @property
    def kits_completos(self): return KitUnidade.query.filter_by(escola=self.nome, status_atual='Completo').count()
    @property
    def saude_media(self):
        valores = [k.saude_percentual for k in self.kits if k.saude_percentual is not None]
        return round(sum(valores) / len(valores), 1) if valores else None


# ─────────────────────────────────────────────
#  CONTEXT PROCESSOR
# ─────────────────────────────────────────────
@app.context_processor
def inject_globals():
    return {'now': datetime.now()}


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def allowed_file(filename):
    return ('.' in filename and
            filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS)


def usuario_atual():
    uid = session.get('user_id')
    return Usuario.query.get(uid) if uid else None


def escolas_do_usuario(user):
    """Retorna lista de escolas acessíveis, ou None para acesso total."""
    if not user or user.escola in (None, '', 'Todas'):
        return None
    return [e.strip() for e in user.escola.split(',')]


def _calcular_stats(unidades):
    """Retorna (total, completos, incompletos, pendentes)."""
    total = len(unidades)
    completos = sum(1 for u in unidades if u.status_atual == 'Completo')
    incompletos = sum(1 for u in unidades if u.status_atual == 'Incompleto')
    pendentes = total - completos - incompletos
    return total, completos, incompletos, pendentes


def _calcular_ranking_perdas(unidades, top=10):
    """Ranking de peças mais perdidas nas últimas conferências."""
    contagem = {}
    for u in unidades:
        uc = u.ultima_conferencia
        if not uc: continue
        for d in uc.detalhes:
            if not d.peca: continue
            pid = d.peca_id
            if pid not in contagem:
                contagem[pid] = {'nome': d.peca.nome, 'codigo': d.peca.codigo_lego,
                                  'perdas': 0, 'kits': 0}
            diff = d.quantidade_encontrada - d.quantidade_esperada_na_epoca
            if diff < 0:
                contagem[pid]['perdas'] += abs(diff)
                contagem[pid]['kits'] += 1
    return sorted(contagem.values(), key=lambda x: x['perdas'], reverse=True)[:top]


def _imagem_peca_path(imagem_url):
    """Caminho absoluto da imagem de peça, ou None se não existir."""
    if not imagem_url or imagem_url in ('sem-foto.png', 'kit-default.png'):
        return None
    path = os.path.join(app.config['UPLOAD_FOLDER'], imagem_url)
    return path if os.path.exists(path) else None


def _filtrar_detalhes(detalhes, modo):
    """Filtra detalhes: 'completo'=todos, 'faltantes'=só com falta."""
    if modo == 'faltantes':
        return [d for d in detalhes
                if d.quantidade_encontrada < d.quantidade_esperada_na_epoca]
    return list(detalhes)


# ─────────────────────────────────────────────
#  DECORADORES DE ACESSO
# ─────────────────────────────────────────────
def login_required(roles=None):
    def decorator(f):
        from functools import wraps
        @wraps(f)
        def wrapped(*args, **kwargs):
            if 'user_id' not in session:
                flash('Faça login para acessar esta página.', 'warning')
                return redirect(url_for('login'))
            user = usuario_atual()
            if not user or not user.ativo:
                session.clear()
                flash('Conta desativada ou não encontrada.', 'danger')
                return redirect(url_for('login'))
            if roles:
                allowed = [roles] if isinstance(roles, str) else roles
                if user.role not in allowed:
                    logger.warning("Acesso negado: user=%s role=%s tentou %s",
                                   user.username, user.role, request.path)
                    abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator


# ─────────────────────────────────────────────
#  AUTENTICAÇÃO
# ─────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = Usuario.query.filter_by(username=username).first()
        if user and user.ativo and user.check_password(password):
            session.permanent = True
            session['user_id'] = user.id
            session['user_role'] = user.role
            session['username'] = user.username
            session['user_escola'] = user.escola or ''
            user.ultimo_login = datetime.now(timezone.utc)
            db.session.commit()
            logger.info("Login: %s (%s)", user.username, user.role)
            flash(f'Bem-vindo, {user.username}!', 'success')
            dest = {'admin': 'dashboard', 'pedagogo': 'dashboard_pedagogo',
                    'auxiliar': 'dashboard_auxiliar'}.get(user.role, 'login')
            return redirect(url_for(dest))
        flash('Usuário ou senha inválidos.', 'danger')
        logger.warning("Falha de login para username='%s'", username)
    return render_template('login.html')


@app.route('/logout')
def logout():
    uname = session.get('username', '?')
    session.clear()
    logger.info("Logout: %s", uname)
    flash('Sessão encerrada com sucesso.', 'info')
    return redirect(url_for('login'))


@app.route('/trocar-senha', methods=['GET', 'POST'])
@login_required()
def trocar_senha():
    user = usuario_atual()
    if request.method == 'POST':
        senha_atual = request.form.get('senha_atual', '')
        nova = request.form.get('nova_senha', '')
        confirmacao = request.form.get('confirmacao', '')
        if not user.check_password(senha_atual):
            flash('Senha atual incorreta.', 'danger')
        elif len(nova) < 6:
            flash('A nova senha deve ter pelo menos 6 caracteres.', 'danger')
        elif nova != confirmacao:
            flash('As senhas não coincidem.', 'danger')
        else:
            user.set_password(nova)
            db.session.commit()
            flash('Senha alterada com sucesso!', 'success')
            return redirect(url_for('index'))
    return render_template('trocar_senha.html')


# ─────────────────────────────────────────────
#  INDEX
# ─────────────────────────────────────────────
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    role = session.get('user_role')
    dest = {'admin': 'dashboard', 'pedagogo': 'dashboard_pedagogo',
            'auxiliar': 'dashboard_auxiliar'}.get(role, 'login')
    return redirect(url_for(dest))


# ─────────────────────────────────────────────
#  DASHBOARDS
# ─────────────────────────────────────────────
@app.route('/admin/dashboard')
@login_required(roles=['admin', 'pedagogo'])
def dashboard():
    unidades = KitUnidade.query.all()
    modelos = KitModelo.query.all()
    total, completos, incompletos, pendentes = _calcular_stats(unidades)
    escolas_distintas = db.session.query(KitUnidade.escola).distinct().count()
    return render_template('admin/dashboard.html',
                           modelos=modelos, unidades=unidades,
                           total=total, completos=completos,
                           incompletos=incompletos, pendentes=pendentes,
                           escolas_distintas=escolas_distintas)


@app.route('/pedagogo/dashboard')
@login_required(roles='pedagogo')
def dashboard_pedagogo():
    user = usuario_atual()
    acesso = escolas_do_usuario(user)
    if acesso is None:
        unidades = KitUnidade.query.all()
        escolas_obj = Escola.query.filter_by(ativo=True).order_by(Escola.nome).all()
    else:
        unidades = KitUnidade.query.filter(KitUnidade.escola.in_(acesso)).all()
        escolas_obj = Escola.query.filter(Escola.nome.in_(acesso)).order_by(Escola.nome).all()

    total, completos, incompletos, pendentes = _calcular_stats(unidades)
    kit_ids = [u.id for u in unidades]
    ultimas_confs = []
    if kit_ids:
        ultimas_confs = (Conferencia.query
                         .filter(Conferencia.kit_unidade_id.in_(kit_ids))
                         .order_by(Conferencia.data_conferencia.desc())
                         .limit(10).all())
    ranking_perdas = _calcular_ranking_perdas(unidades)
    return render_template('pedagogo/dashboard.html',
                           unidades=unidades, escolas_obj=escolas_obj,
                           total=total, completos=completos,
                           incompletos=incompletos, pendentes=pendentes,
                           ultimas_confs=ultimas_confs, ranking_perdas=ranking_perdas)


@app.route('/auxiliar/dashboard')
@login_required(roles='auxiliar')
def dashboard_auxiliar():
    user = usuario_atual()
    minha_escola = user.escola or ''
    unidades = KitUnidade.query.filter_by(escola=minha_escola).all()
    completos = sum(1 for u in unidades if u.status_atual == 'Completo')
    incompletos = len(unidades) - completos
    # Modelos que têm unidades nesta escola (auxiliar pode editar)
    modelo_ids = list({u.kit_modelo_id for u in unidades if u.kit_modelo_id})
    modelos_editaveis = KitModelo.query.filter(KitModelo.id.in_(modelo_ids)).all() if modelo_ids else []
    return render_template('auxiliar/dashboard.html',
                           unidades=unidades, escola=minha_escola,
                           completos=completos, incompletos=incompletos,
                           modelos_editaveis=modelos_editaveis)


# ─────────────────────────────────────────────
#  USUÁRIOS
# ─────────────────────────────────────────────
@app.route('/admin/usuarios')
@login_required(roles='admin')
def gerenciar_usuarios():
    usuarios = Usuario.query.order_by(Usuario.username).all()
    return render_template('admin/usuarios.html', usuarios=usuarios)


@app.route('/admin/usuario/novo', methods=['GET', 'POST'])
@login_required(roles='admin')
def novo_usuario():
    escolas_lista = Escola.query.filter_by(ativo=True).order_by(Escola.nome).all()
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        senha = request.form.get('password', '')
        role = request.form.get('role', 'auxiliar')
        escola = request.form.get('escola', '').strip() or 'Todas'
        if not username or not senha:
            flash('Usuário e senha são obrigatórios.', 'danger')
            return redirect(url_for('novo_usuario'))
        if len(senha) < 6:
            flash('A senha deve ter pelo menos 6 caracteres.', 'danger')
            return redirect(url_for('novo_usuario'))
        if Usuario.query.filter_by(username=username).first():
            flash(f'O usuário "{username}" já existe.', 'danger')
            return redirect(url_for('novo_usuario'))
        novo = Usuario(username=username, role=role, escola=escola)
        novo.set_password(senha)
        db.session.add(novo)
        db.session.commit()
        flash(f'Usuário {username} cadastrado!', 'success')
        return redirect(url_for('gerenciar_usuarios'))
    return render_template('admin/form_usuario.html', usuario=None, escolas_lista=escolas_lista)


@app.route('/admin/usuario/<int:uid>/editar', methods=['GET', 'POST'])
@login_required(roles='admin')
def editar_usuario(uid):
    user = Usuario.query.get_or_404(uid)
    escolas_lista = Escola.query.filter_by(ativo=True).order_by(Escola.nome).all()
    if request.method == 'POST':
        user.role = request.form.get('role', user.role)
        user.escola = request.form.get('escola', '').strip() or 'Todas'
        user.ativo = 'ativo' in request.form
        nova_senha = request.form.get('nova_senha', '').strip()
        if nova_senha:
            if len(nova_senha) < 6:
                flash('A nova senha deve ter pelo menos 6 caracteres.', 'danger')
                return redirect(url_for('editar_usuario', uid=uid))
            user.set_password(nova_senha)
        db.session.commit()
        flash(f'Usuário {user.username} atualizado!', 'success')
        return redirect(url_for('gerenciar_usuarios'))
    return render_template('admin/form_usuario.html', usuario=user, escolas_lista=escolas_lista)


@app.route('/admin/usuario/<int:uid>/toggle', methods=['POST'])
@login_required(roles='admin')
def toggle_usuario(uid):
    user = Usuario.query.get_or_404(uid)
    if user.id == session['user_id']:
        flash('Você não pode desativar sua própria conta.', 'danger')
        return redirect(url_for('gerenciar_usuarios'))
    user.ativo = not user.ativo
    db.session.commit()
    flash(f'Usuário {user.username} {"ativado" if user.ativo else "desativado"}.', 'info')
    return redirect(url_for('gerenciar_usuarios'))


@app.route('/admin/usuario/<int:uid>/deletar', methods=['POST'])
@login_required(roles='admin')
def deletar_usuario(uid):
    user = Usuario.query.get_or_404(uid)
    # Proteção 1: não pode deletar a si mesmo
    if user.id == session['user_id']:
        flash('Você não pode excluir sua própria conta.', 'danger')
        return redirect(url_for('gerenciar_usuarios'))
    # Proteção 2: não pode deletar o último admin
    if user.role == 'admin':
        total_admins = Usuario.query.filter_by(role='admin').count()
        if total_admins <= 1:
            flash('Não é possível excluir o único administrador do sistema.', 'danger')
            return redirect(url_for('gerenciar_usuarios'))
    username = user.username
    db.session.delete(user)
    db.session.commit()
    flash(f'Usuário "{username}" excluído com sucesso.', 'success')
    return redirect(url_for('gerenciar_usuarios'))


# ─────────────────────────────────────────────
#  ESCOLAS
# ─────────────────────────────────────────────
@app.route('/admin/escolas')
@login_required(roles=['admin', 'pedagogo'])
def lista_escolas():
    user = usuario_atual()
    acesso = escolas_do_usuario(user)
    if acesso:
        escolas_obj = Escola.query.filter(Escola.nome.in_(acesso)).order_by(Escola.nome).all()
    else:
        escolas_obj = Escola.query.order_by(Escola.nome).all()
    escolas_raw = db.session.query(
        KitUnidade.escola,
        db.func.count(KitUnidade.id).label('total'),
        db.func.sum(db.case((KitUnidade.status_atual == 'Completo', 1), else_=0)).label('completos')
    ).group_by(KitUnidade.escola).all()
    stats = {e[0]: {'total': e[1], 'completos': e[2] or 0} for e in escolas_raw}
    return render_template('admin/lista_escolas.html', escolas_obj=escolas_obj, stats=stats)


@app.route('/admin/escola/nova', methods=['GET', 'POST'])
@login_required(roles='admin')
def nova_escola():
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        if not nome:
            flash('Nome da escola é obrigatório.', 'danger')
            return redirect(url_for('nova_escola'))
        if Escola.query.filter_by(nome=nome).first():
            flash(f'Escola "{nome}" já está cadastrada.', 'danger')
            return redirect(url_for('nova_escola'))
        db.session.add(Escola(nome=nome,
                               cidade=request.form.get('cidade', '').strip(),
                               responsavel=request.form.get('responsavel', '').strip(),
                               telefone=request.form.get('telefone', '').strip()))
        db.session.commit()
        flash(f'Escola "{nome}" cadastrada!', 'success')
        return redirect(url_for('lista_escolas'))
    return render_template('admin/form_escola.html', escola=None)


@app.route('/admin/escola/<int:eid>/editar', methods=['GET', 'POST'])
@login_required(roles='admin')
def editar_escola(eid):
    escola = Escola.query.get_or_404(eid)
    if request.method == 'POST':
        nome_antigo = escola.nome
        escola.nome = request.form.get('nome', escola.nome).strip()
        escola.cidade = request.form.get('cidade', '').strip()
        escola.responsavel = request.form.get('responsavel', '').strip()
        escola.telefone = request.form.get('telefone', '').strip()
        escola.ativo = 'ativo' in request.form
        if escola.nome != nome_antigo:
            KitUnidade.query.filter_by(escola=nome_antigo).update({'escola': escola.nome})
            Usuario.query.filter_by(escola=nome_antigo).update({'escola': escola.nome})
        db.session.commit()
        flash('Escola atualizada!', 'success')
        return redirect(url_for('lista_escolas'))
    return render_template('admin/form_escola.html', escola=escola)


@app.route('/admin/escola/<int:eid>/deletar', methods=['POST'])
@login_required(roles='admin')
def deletar_escola(eid):
    escola = Escola.query.get_or_404(eid)
    if KitUnidade.query.filter_by(escola=escola.nome).count() > 0:
        flash('Não é possível deletar: existem kits vinculados.', 'danger')
        return redirect(url_for('lista_escolas'))
    db.session.delete(escola)
    db.session.commit()
    flash(f'Escola "{escola.nome}" removida.', 'success')
    return redirect(url_for('lista_escolas'))


@app.route('/admin/escola/<path:nome_escola>/detalhe')
@login_required(roles=['admin', 'pedagogo'])
def detalhe_escola(nome_escola):
    user = usuario_atual()
    if user.role == 'pedagogo':
        acesso = escolas_do_usuario(user)
        if acesso and nome_escola not in acesso:
            abort(403)
    unidades = (KitUnidade.query.filter_by(escola=nome_escola)
                .order_by(KitUnidade.identificador).all())
    escola_obj = Escola.query.filter_by(nome=nome_escola).first()
    modelos_kits = {}
    for u in unidades:
        modelos_kits.setdefault(u.modelo.nome if u.modelo else 'Sem modelo', []).append(u)
    total, completos, incompletos, pendentes = _calcular_stats(unidades)
    ranking = _calcular_ranking_perdas(unidades)
    return render_template('admin/detalhe_escola.html',
                           nome_escola=nome_escola, escola_obj=escola_obj,
                           unidades=unidades, modelos_kits=modelos_kits,
                           total=total, completos=completos,
                           incompletos=incompletos, pendentes=pendentes,
                           ranking=ranking)


@app.route('/admin/escola/<path:nome_escola>')
@login_required(roles=['admin', 'pedagogo'])
def detalhes_escola(nome_escola):
    return redirect(url_for('detalhe_escola', nome_escola=nome_escola))


@app.route('/api/escolas')
@login_required(roles=['admin', 'pedagogo'])
def api_escolas():
    escolas = Escola.query.filter_by(ativo=True).order_by(Escola.nome).all()
    return jsonify([{'id': e.id, 'nome': e.nome} for e in escolas])


# ─────────────────────────────────────────────
#  PEÇAS
# ─────────────────────────────────────────────
@app.route('/admin/pecas')
@login_required(roles=['admin', 'pedagogo', 'auxiliar'])
def listar_pecas():
    pecas = Peca.query.order_by(Peca.nome).all()
    return render_template('admin/lista_pecas.html', pecas=pecas)


@app.route('/admin/pecas/novo', methods=['GET', 'POST'])
@login_required(roles=['admin', 'auxiliar'])
def nova_peca():
    if request.method == 'POST':
        codigo = request.form.get('codigo_lego', '').strip()
        nome = request.form.get('nome', '').strip()
        arquivo = request.files.get('foto')
        if not codigo or not nome:
            flash('Código e nome são obrigatórios.', 'danger')
            return redirect(url_for('nova_peca'))
        if Peca.query.filter_by(codigo_lego=codigo).first():
            flash(f'Código {codigo} já cadastrado.', 'danger')
            return redirect(url_for('nova_peca'))
        filename = 'sem-foto.png'
        if arquivo and arquivo.filename:
            if not allowed_file(arquivo.filename):
                flash('Formato inválido. Use PNG, JPG ou JPEG.', 'danger')
                return redirect(url_for('nova_peca'))
            ext = arquivo.filename.rsplit('.', 1)[1].lower()
            filename = secure_filename(f"{codigo}.{ext}")
            arquivo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        db.session.add(Peca(codigo_lego=codigo, nome=nome, imagem_url=filename))
        db.session.commit()
        flash('Peça cadastrada com sucesso!', 'success')
        return redirect(url_for('listar_pecas'))
    return render_template('admin/form_peca.html', peca=None)


@app.route('/admin/pecas/<int:pid>/editar', methods=['GET', 'POST'])
@login_required(roles='admin')
def editar_peca(pid):
    peca = Peca.query.get_or_404(pid)
    if request.method == 'POST':
        peca.nome = request.form.get('nome', peca.nome).strip()
        arquivo = request.files.get('foto')
        if arquivo and arquivo.filename:
            if not allowed_file(arquivo.filename):
                flash('Formato inválido. Use PNG, JPG ou JPEG.', 'danger')
                return redirect(url_for('editar_peca', pid=pid))
            ext = arquivo.filename.rsplit('.', 1)[1].lower()
            filename = secure_filename(f"{peca.codigo_lego}.{ext}")
            arquivo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            peca.imagem_url = filename
        db.session.commit()
        flash('Peça atualizada!', 'success')
        return redirect(url_for('listar_pecas'))
    return render_template('admin/form_peca.html', peca=peca)


@app.route('/admin/pecas/<int:pid>/deletar', methods=['POST'])
@login_required(roles='admin')
def deletar_peca(pid):
    peca = Peca.query.get_or_404(pid)
    try:
        if peca.imagem_url and peca.imagem_url != 'sem-foto.png':
            caminho = os.path.join(app.config['UPLOAD_FOLDER'], peca.imagem_url)
            if os.path.exists(caminho): os.remove(caminho)
        db.session.delete(peca)
        db.session.commit()
        flash(f'Peça {peca.codigo_lego} removida.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Não foi possível remover: peça está vinculada a kits.', 'danger')
        logger.error("Erro ao deletar peça %s: %s", pid, e)
    return redirect(url_for('listar_pecas'))


# ─────────────────────────────────────────────
#  MODELOS DE KIT
# ─────────────────────────────────────────────
@app.route('/admin/modelos')
@login_required(roles=['admin', 'pedagogo'])
def listar_modelos():
    return render_template('admin/lista_modelos.html', modelos=KitModelo.query.all())


@app.route('/admin/modelo/novo', methods=['GET', 'POST'])
@login_required(roles=['admin', 'auxiliar'])
def novo_modelo():
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        if not nome:
            flash('Nome é obrigatório.', 'danger')
            return redirect(url_for('novo_modelo'))
        arquivo = request.files.get('foto')
        filename = 'kit-default.png'
        if arquivo and arquivo.filename and allowed_file(arquivo.filename):
            ext = arquivo.filename.rsplit('.', 1)[1].lower()
            filename = secure_filename(f"kit_{nome.replace(' ', '_')}.{ext}")
            arquivo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        novo = KitModelo(nome=nome, categoria=request.form.get('categoria', '').strip(),
                          foto_capa=filename)
        db.session.add(novo)
        db.session.commit()
        flash(f'Modelo "{nome}" criado!', 'success')
        return redirect(url_for('gerenciar_composicao', modelo_id=novo.id))
    return render_template('admin/form_modelo.html', modelo=None)


@app.route('/admin/modelo/<int:mid>/editar', methods=['GET', 'POST'])
@login_required(roles=['admin', 'auxiliar'])
def editar_modelo(mid):
    modelo = KitModelo.query.get_or_404(mid)
    user = usuario_atual()
    # Auxiliar só pode editar modelos que têm unidades na sua escola
    if user.role == 'auxiliar':
        tem_unidade = KitUnidade.query.filter_by(kit_modelo_id=mid, escola=user.escola).first()
        if not tem_unidade:
            flash('Você não tem permissão para editar este modelo.', 'danger')
            return redirect(url_for('dashboard_auxiliar'))
    if request.method == 'POST':
        modelo.nome = request.form.get('nome', modelo.nome).strip()
        modelo.categoria = request.form.get('categoria', modelo.categoria).strip()
        arquivo = request.files.get('foto')
        if arquivo and arquivo.filename and allowed_file(arquivo.filename):
            ext = arquivo.filename.rsplit('.', 1)[1].lower()
            filename = secure_filename(f"kit_{modelo.nome.replace(' ', '_')}.{ext}")
            arquivo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            modelo.foto_capa = filename
        db.session.commit()
        flash('Modelo atualizado!', 'success')
        destino = url_for('dashboard_auxiliar') if user.role == 'auxiliar' else url_for('listar_modelos')
        return redirect(destino)
    destino_voltar = url_for('dashboard_auxiliar') if user.role == 'auxiliar' else url_for('listar_modelos')
    return render_template('admin/form_modelo.html', modelo=modelo, destino_voltar=destino_voltar)


@app.route('/admin/modelo/<int:mid>/deletar', methods=['POST'])
@login_required(roles='admin')
def deletar_modelo(mid):
    modelo = KitModelo.query.get_or_404(mid)
    if modelo.unidades_reais:
        flash('Não é possível deletar: existem unidades físicas vinculadas.', 'danger')
        return redirect(url_for('listar_modelos'))
    db.session.delete(modelo)
    db.session.commit()
    flash(f'Modelo "{modelo.nome}" removido.', 'success')
    return redirect(url_for('listar_modelos'))


@app.route('/admin/modelo/<int:modelo_id>/composicao', methods=['GET', 'POST'])
@login_required(roles=['admin', 'auxiliar'])
def gerenciar_composicao(modelo_id):
    modelo = KitModelo.query.get_or_404(modelo_id)
    pecas_catalogo = Peca.query.order_by(Peca.nome).all()
    if request.method == 'POST':
        peca_id = request.form.get('peca_id')
        try:
            quantidade = int(request.form.get('quantidade', 1))
        except ValueError:
            flash('Quantidade inválida.', 'danger')
            return redirect(url_for('gerenciar_composicao', modelo_id=modelo_id))
        if quantidade < 1:
            flash('Quantidade deve ser maior que zero.', 'danger')
            return redirect(url_for('gerenciar_composicao', modelo_id=modelo_id))
        item = ComposicaoKit.query.filter_by(kit_modelo_id=modelo_id, peca_id=peca_id).first()
        if item:
            item.quantidade_esperada = quantidade
        else:
            db.session.add(ComposicaoKit(kit_modelo_id=modelo_id, peca_id=peca_id,
                                          quantidade_esperada=quantidade))
        db.session.commit()
        flash('Composição atualizada!', 'success')
        return redirect(url_for('gerenciar_composicao', modelo_id=modelo_id))
    return render_template('admin/gerenciar_pecas.html', modelo=modelo, pecas_catalogo=pecas_catalogo)


@app.route('/admin/composicao/remover/<int:item_id>', methods=['POST'])
@login_required(roles=['admin', 'auxiliar'])
def remover_item_composicao(item_id):
    item = ComposicaoKit.query.get_or_404(item_id)
    modelo_id = item.kit_modelo_id
    db.session.delete(item)
    db.session.commit()
    flash('Peça removida do kit.', 'info')
    return redirect(url_for('gerenciar_composicao', modelo_id=modelo_id))


@app.route('/api/composicao/ajustar_quantidade', methods=['POST'])
@login_required(roles=['admin', 'auxiliar'])
def ajustar_quantidade_composicao():
    data = request.get_json()
    item_id = data.get('item_id')
    delta = data.get('delta')

    item = ComposicaoKit.query.get_or_404(item_id)
    nova_qtd = item.quantidade_esperada + delta

    if nova_qtd < 1:
        return jsonify({'status': 'erro', 'mensagem': 'A quantidade mínima é 1.'}), 400

    item.quantidade_esperada = nova_qtd
    db.session.commit()

    return jsonify({
        'status': 'sucesso',
        'nova_quantidade': item.quantidade_esperada
    })


# ─────────────────────────────────────────────
#  UNIDADES FÍSICAS
# ─────────────────────────────────────────────
@app.route('/admin/unidades')
@login_required(roles=['admin', 'pedagogo'])
def listar_unidades():
    escola_filtro = request.args.get('escola', '')
    query = KitUnidade.query
    if escola_filtro: query = query.filter_by(escola=escola_filtro)
    unidades = query.order_by(KitUnidade.escola, KitUnidade.identificador).all()
    escolas = [e[0] for e in db.session.query(KitUnidade.escola).distinct().all()]
    return render_template('admin/unidades.html', unidades=unidades,
                           escolas=escolas, escola_filtro=escola_filtro)


@app.route('/admin/unidade/novo', methods=['GET', 'POST'])
@login_required(roles='admin')
def nova_unidade():
    modelos = KitModelo.query.all()
    escolas_lista = Escola.query.filter_by(ativo=True).order_by(Escola.nome).all()
    if request.method == 'POST':
        identificador = request.form.get('identificador', '').strip()
        escola = request.form.get('escola', '').strip()
        modelo_id = request.form.get('modelo_id')
        if not identificador or not escola or not modelo_id:
            flash('Todos os campos são obrigatórios.', 'danger')
            return redirect(url_for('nova_unidade'))
        db.session.add(KitUnidade(identificador=identificador, escola=escola,
                                   kit_modelo_id=modelo_id, status_atual='Pendente'))
        db.session.commit()
        flash(f'Unidade {identificador} criada!', 'success')
        return redirect(url_for('listar_unidades'))
    return render_template('admin/form_unidade.html', modelos=modelos,
                           escolas_lista=escolas_lista, unidade=None)


@app.route('/admin/unidade/<int:uid>/editar', methods=['GET', 'POST'])
@login_required(roles=['admin', 'auxiliar'])
def editar_unidade(uid):
    unidade = KitUnidade.query.get_or_404(uid)
    user = usuario_atual()
    # Auxiliar só pode editar unidades da sua escola
    if user.role == 'auxiliar' and unidade.escola != user.escola:
        flash('Você só pode editar unidades da sua escola.', 'danger')
        return redirect(url_for('conferencias_auxiliar'))
    modelos = KitModelo.query.all()
    escolas_lista = Escola.query.filter_by(ativo=True).order_by(Escola.nome).all()
    if request.method == 'POST':
        unidade.identificador = request.form.get('identificador', '').strip()
        # Auxiliar não pode mudar a escola da unidade
        if user.role != 'auxiliar':
            unidade.escola = request.form.get('escola', '').strip()
        unidade.kit_modelo_id = request.form.get('modelo_id', unidade.kit_modelo_id)
        db.session.commit()
        flash('Unidade atualizada!', 'success')
        destino = url_for('conferencias_auxiliar') if user.role == 'auxiliar' else url_for('listar_unidades')
        return redirect(destino)
    destino_voltar = url_for('conferencias_auxiliar') if user.role == 'auxiliar' else url_for('listar_unidades')
    return render_template('admin/form_unidade.html', modelos=modelos,
                           escolas_lista=escolas_lista, unidade=unidade,
                           destino_voltar=destino_voltar, is_auxiliar=(user.role == 'auxiliar'))


@app.route('/admin/unidade/<int:uid>/deletar', methods=['POST'])
@login_required(roles='admin')
def deletar_unidade(uid):
    unidade = KitUnidade.query.get_or_404(uid)
    db.session.delete(unidade)
    db.session.commit()
    flash(f'Unidade {unidade.identificador} removida.', 'success')
    return redirect(url_for('listar_unidades'))


# ─────────────────────────────────────────────
#  CONFERÊNCIA
# ─────────────────────────────────────────────
@app.route('/conferir/<int:kit_id>', methods=['GET', 'POST'])
@login_required(roles=['auxiliar', 'admin', 'pedagogo'])
def conferir_kit(kit_id):
    unidade = KitUnidade.query.get_or_404(kit_id)
    user = usuario_atual()
    if user.role == 'auxiliar' and unidade.escola != user.escola:
        abort(403)
    itens = unidade.modelo.pecas_obrigatorias
    if request.method == 'POST':
        nova_conf = Conferencia(
            kit_unidade_id=kit_id,
            responsavel=user.username,
            observacoes=request.form.get('observacoes', '').strip(),
            status_resultado=request.form.get('status_geral', 'Incompleto'))
        db.session.add(nova_conf)
        todas_completas = True
        for item in itens:
            try:
                qtd = int(request.form.get(f'peca_{item.peca.id}', 0))
            except ValueError:
                qtd = 0
            obs_peca = request.form.get(f'obs_{item.peca.id}', '').strip() or None
            if qtd < item.quantidade_esperada: todas_completas = False
            db.session.add(ConferenciaDetalhe(
                conferencia=nova_conf, peca_id=item.peca.id,
                quantidade_esperada_na_epoca=item.quantidade_esperada,
                quantidade_encontrada=qtd, observacao_peca=obs_peca))
        unidade.status_atual = 'Completo' if todas_completas else 'Incompleto'
        db.session.commit()
        flash(f'Conferência salva! Status: {unidade.status_atual}', 'success')
        if user.role == 'auxiliar':
            return redirect(url_for('conferencias_auxiliar'))
        return redirect(url_for('historico_kit_auxiliar', kit_id=kit_id))
    return render_template('auxiliar/conferir.html', unidade=unidade, itens=itens)


# ─────────────────────────────────────────────
#  CENTRAL DE CONFERÊNCIAS — AUXILIAR
# ─────────────────────────────────────────────
@app.route('/auxiliar/conferencias')
@login_required(roles='auxiliar')
def conferencias_auxiliar():
    user = usuario_atual()
    unidades = (KitUnidade.query.filter_by(escola=user.escola)
                .order_by(KitUnidade.identificador).all())
    total, completos, incompletos, pendentes = _calcular_stats(unidades)
    total_perdidas = sum(
        abs(d.quantidade_encontrada - d.quantidade_esperada_na_epoca)
        for u in unidades
        for d in (u.ultima_conferencia.detalhes if u.ultima_conferencia else [])
        if d.quantidade_encontrada < d.quantidade_esperada_na_epoca
    )
    return render_template('auxiliar/conferencias.html',
                           unidades=unidades, escola=user.escola,
                           total=total, completos=completos,
                           incompletos=incompletos, pendentes=pendentes,
                           total_perdidas=total_perdidas)


@app.route('/auxiliar/kit/<int:kit_id>/historico')
@login_required(roles=['auxiliar', 'admin', 'pedagogo'])
def historico_kit_auxiliar(kit_id):
    unidade = KitUnidade.query.get_or_404(kit_id)
    user = usuario_atual()
    if user.role == 'auxiliar' and unidade.escola != user.escola:
        abort(403)
    conferencias = (Conferencia.query.filter_by(kit_unidade_id=kit_id)
                    .order_by(Conferencia.data_conferencia.desc()).all())
    labels_graf, valores_graf = [], []
    for c in reversed(conferencias):
        labels_graf.append(c.data_conferencia.strftime('%d/%m/%y'))
        esp = sum(d.quantidade_esperada_na_epoca for d in c.detalhes)
        enc = sum(d.quantidade_encontrada for d in c.detalhes)
        valores_graf.append(round(enc / esp * 100, 1) if esp else 100.0)
    tendencia_pecas = []
    if len(conferencias) >= 2:
        mapa_primeira = {d.peca_id: d.quantidade_encontrada for d in conferencias[-1].detalhes}
        for d in conferencias[0].detalhes:
            qtd_antes = mapa_primeira.get(d.peca_id)
            if qtd_antes is not None and d.peca:
                diff = d.quantidade_encontrada - qtd_antes
                tendencia_pecas.append({'nome': d.peca.nome, 'codigo': d.peca.codigo_lego,
                                        'antes': qtd_antes, 'depois': d.quantidade_encontrada,
                                        'diff': diff})
        tendencia_pecas.sort(key=lambda x: x['diff'])
    return render_template('auxiliar/historico_kit.html',
                           unidade=unidade, conferencias=conferencias,
                           labels_graf=labels_graf, valores_graf=valores_graf,
                           tendencia_pecas=tendencia_pecas)


@app.route('/admin/unidade/<int:uid>/historico')
@login_required(roles=['admin', 'pedagogo'])
def historico_kit(uid):
    return redirect(url_for('historico_kit_auxiliar', kit_id=uid))


@app.route('/auxiliar/comparar')
@login_required(roles=['auxiliar', 'admin', 'pedagogo'])
def comparar_kits():
    user = usuario_atual()
    if user.role == 'auxiliar':
        unidades = KitUnidade.query.filter_by(escola=user.escola).order_by(KitUnidade.identificador).all()
        escola = user.escola
    else:
        escola = request.args.get('escola', '')
        unidades = (KitUnidade.query.filter_by(escola=escola).order_by(KitUnidade.identificador).all()
                    if escola else KitUnidade.query.order_by(KitUnidade.identificador).all())
    datasets = []
    todas_labels = set()
    for u in unidades:
        confs = Conferencia.query.filter_by(kit_unidade_id=u.id).order_by(Conferencia.data_conferencia.asc()).all()
        if not confs: continue
        pontos = {}
        for c in confs:
            label = c.data_conferencia.strftime('%d/%m/%y')
            todas_labels.add(label)
            esp = sum(d.quantidade_esperada_na_epoca for d in c.detalhes)
            enc = sum(d.quantidade_encontrada for d in c.detalhes)
            pontos[label] = round(enc / esp * 100, 1) if esp else 100.0
        datasets.append({'id': u.id, 'nome': u.identificador,
                         'pontos': pontos, 'status': u.status_atual, 'saude': u.saude_percentual})
    labels_ord = sorted(todas_labels, key=lambda s: datetime.strptime(s, '%d/%m/%y'))
    escolas_lista = [e[0] for e in db.session.query(KitUnidade.escola).distinct().all()]
    return render_template('auxiliar/comparar.html',
                           unidades=unidades, datasets=datasets,
                           labels_ord=labels_ord,
                           ranking_perdas=_calcular_ranking_perdas(unidades, top=15),
                           escola=escola, escolas_lista=escolas_lista)


# ─────────────────────────────────────────────
#  ETIQUETAS QR CODE
# ─────────────────────────────────────────────
@app.route('/auxiliar/etiquetas')
@login_required(roles=['auxiliar', 'admin'])
def etiquetas_auxiliar():
    user = usuario_atual()
    if user.role == 'auxiliar':
        kits = KitUnidade.query.filter_by(escola=user.escola).all()
        escola = user.escola
    else:
        escola_filtro = request.args.get('escola', '')
        kits = (KitUnidade.query.filter_by(escola=escola_filtro).all()
                if escola_filtro else KitUnidade.query.all())
        escola = escola_filtro or 'Todas'
    return render_template('auxiliar/etiquetas_print.html', kits=kits, escola=escola)


@app.route('/admin/etiquetas')
@login_required(roles='admin')
def etiquetas_admin():
    escolas = [e[0] for e in db.session.query(KitUnidade.escola).distinct().all()]
    return render_template('admin/etiquetas.html', unidades=KitUnidade.query.all(), escolas=escolas)


# ─────────────────────────────────────────────
#  PDF — FUNÇÕES CENTRALIZADAS
# ─────────────────────────────────────────────
def _pdf_styles():
    """Estilos ReportLab reutilizáveis."""
    s = getSampleStyleSheet()
    az = colors.HexColor('#2563EB')
    return {
        's': s,
        'title': ParagraphStyle('t', parent=s['Title'], fontSize=16, textColor=az, spaceAfter=2),
        'sub':   ParagraphStyle('sub', parent=s['Normal'], fontSize=9, textColor=colors.grey, spaceAfter=6),
        'sec':   ParagraphStyle('sec', parent=s['Heading2'], fontSize=11, textColor=az, spaceBefore=10, spaceAfter=4),
        'norm':  s['Normal'],
        'h3':    s['Heading3'],
        'azul':  az,
        'escuro': colors.HexColor('#0F172A'),
        'cinza':  colors.HexColor('#F1F5F9'),
        'verde':  colors.HexColor('#D1FAE5'),
        'verm':   colors.HexColor('#FEE2E2'),
        'borda':  colors.HexColor('#CBD5E1'),
    }


def _img_cell(imagem_url, size_cm=1.2):
    """Célula de imagem para tabela ReportLab."""
    if imagem_url and imagem_url not in ('sem-foto.png', 'kit-default.png'):
        path = os.path.join(app.config['UPLOAD_FOLDER'], imagem_url)
        if os.path.exists(path):
            try:
                return RLImage(path, width=size_cm * cm, height=size_cm * cm)
            except Exception:
                pass
    return Paragraph('—', getSampleStyleSheet()['Normal'])


def _pdf_tabela_pecas(detalhes, st):
    """Constrói tabela de peças com imagens para PDF. Retorna Table ou None."""
    if not detalhes:
        return None
    cols = [1.4*cm, 5.5*cm, 2.4*cm, 1.5*cm, 1.5*cm, 1.5*cm]
    header = [['Foto', 'Peça', 'Código', 'Esp.', 'Enc.', 'Dif.']]
    rows = []
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), st['azul']),
        ('TEXTCOLOR',  (0, 0), (-1, 0), colors.white),
        ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, -1), 8),
        ('ALIGN',      (3, 0), (-1, -1), 'CENTER'),
        ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID',       (0, 0), (-1, -1), 0.4, st['borda']),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]
    sorted_det = sorted(detalhes, key=lambda d: d.quantidade_encontrada - d.quantidade_esperada_na_epoca)
    for i, d in enumerate(sorted_det, start=1):
        diff = d.quantidade_encontrada - d.quantidade_esperada_na_epoca
        rows.append([
            _img_cell(d.peca.imagem_url if d.peca else None),
            d.peca.nome if d.peca else '—',
            d.peca.codigo_lego if d.peca else '—',
            str(d.quantidade_esperada_na_epoca),
            str(d.quantidade_encontrada),
            str(diff) if diff != 0 else '✓',
        ])
        if diff < 0:
            style.append(('BACKGROUND', (0, i), (-1, i), st['verm']))
        elif diff == 0:
            style.append(('BACKGROUND', (0, i), (-1, i), st['verde']))
        else:
            style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#EFF6FF')))
    t = Table(header + rows, colWidths=cols, repeatRows=1)
    t.setStyle(TableStyle(style))
    return t


def _pdf_relatorio_kit(unidade, modo='completo'):
    """PDF completo de um kit com imagens."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    st = _pdf_styles()
    els = []
    modo_label = 'Relatório Completo' if modo == 'completo' else 'Relatório de Faltantes'
    els += [
        Paragraph(f"{modo_label} — Kit: {unidade.identificador}", st['title']),
        Paragraph(f"Modelo: {unidade.modelo.nome if unidade.modelo else '—'}  ·  "
                  f"Escola: {unidade.escola}  ·  Status: {unidade.status_atual}", st['sub']),
        Paragraph(f"Emitido em: {datetime.now().strftime('%d/%m/%Y às %H:%M')}", st['sub']),
        HRFlowable(width='100%', thickness=2, color=st['azul']),
        Spacer(1, 10),
    ]
    conferencias = (Conferencia.query.filter_by(kit_unidade_id=unidade.id)
                    .order_by(Conferencia.data_conferencia.desc()).all())
    if not conferencias:
        els.append(Paragraph("Nenhuma conferência registrada.", st['norm']))
    else:
        saude = unidade.saude_percentual
        els.append(Paragraph("Resumo de Saúde", st['sec']))
        tr = Table([
            ['Total de conferências', str(len(conferencias))],
            ['Saúde atual', f"{saude}%" if saude is not None else '—'],
            ['Última conferência', conferencias[0].data_conferencia.strftime('%d/%m/%Y %H:%M')],
            ['Responsável', conferencias[0].responsavel or '—'],
        ], colWidths=[8*cm, 9*cm])
        tr.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), st['cinza']),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, st['borda']),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        els += [tr, Spacer(1, 12)]
        for idx, conf in enumerate(conferencias):
            els.append(Paragraph(
                f"Conferência #{len(conferencias)-idx}  —  "
                f"{conf.data_conferencia.strftime('%d/%m/%Y %H:%M')}  "
                f"por {conf.responsavel or '—'}  ·  {conf.status_resultado or '—'}", st['sec']))
            if conf.observacoes:
                els.append(Paragraph(f"Obs: {conf.observacoes}", st['sub']))
            detalhes = _filtrar_detalhes(conf.detalhes, modo)
            if not detalhes:
                els.append(Paragraph(
                    "Nenhuma peça faltando." if modo == 'faltantes' else "Sem detalhes.", st['norm']))
            else:
                tbl = _pdf_tabela_pecas(detalhes, st)
                if tbl: els.append(tbl)
            els.append(Spacer(1, 8))
    doc.build(els)
    buffer.seek(0)
    return buffer


def _pdf_relatorio_escola(unidades, titulo, autor, modo='completo'):
    """PDF consolidado de múltiplos kits com imagens."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    st = _pdf_styles()
    modo_label = 'Relatório Completo' if modo == 'completo' else 'Relatório de Faltantes'
    els = [
        Paragraph(f"{modo_label} — {titulo}", st['title']),
        Paragraph(f"Emitido por {autor} em {datetime.now().strftime('%d/%m/%Y %H:%M')}", st['sub']),
        HRFlowable(width='100%', thickness=2, color=st['azul']),
        Spacer(1, 10),
        Paragraph("Visão Geral dos Kits", st['sec']),
    ]
    # Tabela resumo
    hdr = [['Kit', 'Modelo', 'Status', 'Saúde', 'Última Conf.', 'Responsável']]
    rows = []
    for u in unidades:
        uc = u.ultima_conferencia
        rows.append([
            u.identificador,
            u.modelo.nome[:20] if u.modelo else '—',
            u.status_atual,
            f"{u.saude_percentual}%" if u.saude_percentual is not None else '—',
            uc.data_conferencia.strftime('%d/%m/%Y') if uc else 'Pendente',
            uc.responsavel if uc else '—',
        ])
    rc = [
        ('BACKGROUND', (0, 0), (-1, 0), st['azul']),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.4, st['borda']),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    for i, row in enumerate(rows, start=1):
        if row[2] == 'Incompleto':
            rc.append(('BACKGROUND', (2, i), (2, i), st['verm']))
        elif row[2] == 'Completo':
            rc.append(('BACKGROUND', (2, i), (2, i), st['verde']))
    tbl_resumo = Table(hdr + rows,
                       colWidths=[3.5*cm, 4*cm, 2.5*cm, 1.8*cm, 2.8*cm, 2.9*cm], repeatRows=1)
    tbl_resumo.setStyle(TableStyle(rc))
    els += [tbl_resumo, Spacer(1, 14)]

    # Detalhes por kit
    titulo_det = "Detalhes por Kit" if modo == 'completo' else "Peças Faltantes por Kit"
    els.append(Paragraph(titulo_det, st['sec']))
    for u in unidades:
        uc = u.ultima_conferencia
        if not uc or not uc.detalhes: continue
        detalhes = _filtrar_detalhes(uc.detalhes, modo)
        if not detalhes: continue
        els.append(Paragraph(
            f"{u.identificador}  —  {uc.data_conferencia.strftime('%d/%m/%Y')}  "
            f"·  {uc.responsavel or '—'}  ·  {uc.status_resultado or '—'}", st['h3']))
        tbl = _pdf_tabela_pecas(detalhes, st)
        if tbl: els.append(tbl)
        els.append(Spacer(1, 8))

    els.append(Paragraph(f"Total de kits: {len(unidades)}", st['norm']))
    doc.build(els)
    buffer.seek(0)
    return buffer


# ─────────────────────────────────────────────
#  ROTAS DE RELATÓRIO — HTML (tela/impressão)
# ─────────────────────────────────────────────
def _modo_valido(modo):
    return modo if modo in ('completo', 'faltantes') else 'completo'


@app.route('/relatorio/kit/<int:kit_id>')
@login_required(roles=['admin', 'pedagogo', 'auxiliar'])
def relatorio_kit_view(kit_id):
    """View HTML de relatório de um kit — completo ou faltantes."""
    unidade = KitUnidade.query.get_or_404(kit_id)
    user = usuario_atual()
    if user.role == 'auxiliar' and unidade.escola != user.escola:
        abort(403)
    modo = _modo_valido(request.args.get('modo', 'completo'))
    conferencia = unidade.ultima_conferencia
    detalhes = _filtrar_detalhes(conferencia.detalhes, modo) if conferencia else []
    return render_template('relatorio.html',
                           unidade=unidade, conferencia=conferencia,
                           detalhes=detalhes, modo=modo,
                           titulo=f"Kit {unidade.identificador} — {unidade.escola}",
                           tipo='kit')


@app.route('/relatorio/escola/<path:nome_escola>')
@login_required(roles=['admin', 'pedagogo', 'auxiliar'])
def relatorio_escola_view(nome_escola):
    """View HTML de relatório de uma escola — completo ou faltantes."""
    user = usuario_atual()
    if user.role == 'auxiliar' and user.escola != nome_escola:
        abort(403)
    if user.role == 'pedagogo':
        acesso = escolas_do_usuario(user)
        if acesso and nome_escola not in acesso:
            abort(403)
    modo = _modo_valido(request.args.get('modo', 'completo'))
    unidades = (KitUnidade.query.filter_by(escola=nome_escola)
                .order_by(KitUnidade.identificador).all())
    kits_dados = []
    for u in unidades:
        conf = u.ultima_conferencia
        if not conf:
            if modo == 'completo':
                kits_dados.append({'unidade': u, 'conferencia': None, 'detalhes': []})
            continue
        detalhes = _filtrar_detalhes(conf.detalhes, modo)
        if modo == 'faltantes' and not detalhes:
            continue
        kits_dados.append({'unidade': u, 'conferencia': conf, 'detalhes': detalhes})
    return render_template('relatorio.html',
                           kits_dados=kits_dados, modo=modo,
                           titulo=f"Relatório — {nome_escola}",
                           nome_escola=nome_escola, tipo='escola')


# ─────────────────────────────────────────────
#  ROTAS DE RELATÓRIO — PDF
# ─────────────────────────────────────────────
@app.route('/relatorio/kit/<int:kit_id>/pdf')
@login_required(roles=['admin', 'pedagogo', 'auxiliar'])
def relatorio_kit_pdf(kit_id):
    """PDF de um kit — ?modo=completo|faltantes"""
    unidade = KitUnidade.query.get_or_404(kit_id)
    user = usuario_atual()
    if user.role == 'auxiliar' and unidade.escola != user.escola:
        abort(403)
    modo = _modo_valido(request.args.get('modo', 'completo'))
    buffer = _pdf_relatorio_kit(unidade, modo)
    nome_arq = f"relatorio_{modo}_{unidade.identificador.replace(' ', '_')}.pdf"
    return send_file(buffer, as_attachment=True, download_name=nome_arq,
                     mimetype='application/pdf')


@app.route('/relatorio/escola/<path:nome_escola>/pdf')
@login_required(roles=['admin', 'pedagogo', 'auxiliar'])
def relatorio_escola_pdf(nome_escola):
    """PDF de uma escola — ?modo=completo|faltantes"""
    user = usuario_atual()
    if user.role == 'auxiliar' and user.escola != nome_escola:
        abort(403)
    if user.role == 'pedagogo':
        acesso = escolas_do_usuario(user)
        if acesso and nome_escola not in acesso:
            abort(403)
    modo = _modo_valido(request.args.get('modo', 'completo'))
    unidades = KitUnidade.query.filter_by(escola=nome_escola).order_by(KitUnidade.identificador).all()
    buffer = _pdf_relatorio_escola(unidades, titulo=nome_escola, autor=user.username, modo=modo)
    nome_arq = f"relatorio_{modo}_{nome_escola.replace(' ', '_')}.pdf"
    return send_file(buffer, as_attachment=True, download_name=nome_arq,
                     mimetype='application/pdf')


@app.route('/relatorio/geral/pdf')
@login_required(roles=['admin', 'pedagogo'])
def relatorio_geral_pdf():
    """PDF geral — ?modo=completo|faltantes  ?escola="""
    user = usuario_atual()
    acesso = escolas_do_usuario(user)
    escola_filtro = request.args.get('escola', '')
    modo = _modo_valido(request.args.get('modo', 'completo'))
    if escola_filtro:
        unidades = KitUnidade.query.filter_by(escola=escola_filtro).all()
        titulo = escola_filtro
    elif acesso:
        unidades = KitUnidade.query.filter(KitUnidade.escola.in_(acesso)).all()
        titulo = "Escolas Acessíveis"
    else:
        unidades = KitUnidade.query.all()
        titulo = "Todos os Kits"
    buffer = _pdf_relatorio_escola(unidades, titulo=titulo, autor=user.username, modo=modo)
    return send_file(buffer, as_attachment=True,
                     download_name=f"relatorio_geral_{modo}.pdf",
                     mimetype='application/pdf')


# ── Redirects de compatibilidade com URLs antigas ──────────────────────────
@app.route('/auxiliar/relatorio/pdf')
@login_required(roles='auxiliar')
def gerar_relatorio_auxiliar():
    user = usuario_atual()
    return redirect(url_for('relatorio_escola_pdf', nome_escola=user.escola))


@app.route('/auxiliar/kit/<int:kit_id>/relatorio/pdf')
@login_required(roles=['auxiliar', 'admin', 'pedagogo'])
def pdf_kit_individual(kit_id):
    return redirect(url_for('relatorio_kit_pdf', kit_id=kit_id))


@app.route('/auxiliar/relatorio/todos/pdf')
@login_required(roles=['auxiliar', 'admin', 'pedagogo'])
def pdf_todos_kits():
    user = usuario_atual()
    if user.role == 'auxiliar':
        return redirect(url_for('relatorio_escola_pdf', nome_escola=user.escola))
    escola = request.args.get('escola', '')
    if escola:
        return redirect(url_for('relatorio_escola_pdf', nome_escola=escola))
    return redirect(url_for('relatorio_geral_pdf'))


@app.route('/admin/relatorio/geral')
@login_required(roles=['admin', 'pedagogo'])
def relatorio_geral():
    return redirect(url_for('relatorio_geral_pdf'))


@app.route('/admin/escola/<string:nome_escola>/relatorio')
@login_required(roles=['admin', 'pedagogo'])
def relatorio_escola(nome_escola):
    return redirect(url_for('relatorio_escola_pdf', nome_escola=nome_escola))


# ─────────────────────────────────────────────
#  API JSON INTERNA
# ─────────────────────────────────────────────
@app.route('/api/historico/<int:uid>')
@login_required(roles=['admin', 'pedagogo'])
def api_historico(uid):
    conferencias = (Conferencia.query.filter_by(kit_unidade_id=uid)
                    .order_by(Conferencia.data_conferencia.asc()).all())
    data = {'labels': [], 'valores': [], 'detalhes': []}
    for c in conferencias:
        esp = sum(d.quantidade_esperada_na_epoca for d in c.detalhes)
        enc = sum(d.quantidade_encontrada for d in c.detalhes)
        data['labels'].append(c.data_conferencia.strftime('%d/%m/%Y'))
        data['valores'].append(round(enc / esp * 100, 1) if esp else 100)
        data['detalhes'].append({'responsavel': c.responsavel, 'observacoes': c.observacoes})
    return jsonify(data)


# ─────────────────────────────────────────────
#  BUSCA GLOBAL
# ─────────────────────────────────────────────
@app.route('/busca')
@login_required(roles=['admin', 'pedagogo', 'auxiliar'])
def busca_global():
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'kits': [], 'pecas': [], 'escolas': []})
    user = usuario_atual()
    acesso = escolas_do_usuario(user)
    termo = f'%{q}%'
    qry_kit = KitUnidade.query.filter(
        db.or_(KitUnidade.identificador.ilike(termo), KitUnidade.escola.ilike(termo)))
    if user.role == 'auxiliar':
        qry_kit = qry_kit.filter_by(escola=user.escola)
    elif acesso:
        qry_kit = qry_kit.filter(KitUnidade.escola.in_(acesso))
    kits = qry_kit.limit(8).all()
    pecas = Peca.query.filter(
        db.or_(Peca.nome.ilike(termo), Peca.codigo_lego.ilike(termo))).limit(6).all()
    escolas = []
    if user.role in ('admin', 'pedagogo'):
        qry_e = Escola.query.filter(Escola.nome.ilike(termo))
        if acesso: qry_e = qry_e.filter(Escola.nome.in_(acesso))
        escolas = qry_e.limit(5).all()
    return jsonify({
        'kits': [{'id': u.id, 'nome': u.identificador, 'escola': u.escola,
                  'status': u.status_atual,
                  'url': url_for('historico_kit_auxiliar', kit_id=u.id)} for u in kits],
        'pecas': [{'id': p.id, 'nome': p.nome, 'codigo': p.codigo_lego,
                   'url': url_for('listar_pecas')} for p in pecas],
        'escolas': [{'id': e.id, 'nome': e.nome,
                     'url': url_for('detalhe_escola', nome_escola=e.nome)} for e in escolas],
    })


# ─────────────────────────────────────────────
#  QR CODE SCAN
# ─────────────────────────────────────────────
@app.route('/scan')
@login_required(roles=['auxiliar', 'admin', 'pedagogo'])
def scan_qr():
    return render_template('auxiliar/scan_qr.html')


@app.route('/qr/<int:kit_id>')
@login_required(roles=['auxiliar', 'admin', 'pedagogo'])
def qr_redirect(kit_id):
    unidade = KitUnidade.query.get_or_404(kit_id)
    user = usuario_atual()
    if user.role == 'auxiliar' and unidade.escola != user.escola:
        abort(403)
    return redirect(url_for('conferir_kit', kit_id=kit_id))


# ─────────────────────────────────────────────
#  CONFERÊNCIAS PENDENTES
# ─────────────────────────────────────────────
@app.route('/pendentes')
@login_required(roles=['admin', 'pedagogo', 'auxiliar'])
def conferencias_pendentes():
    user = usuario_atual()
    dias = int(request.args.get('dias', 30))
    if user.role == 'auxiliar':
        unidades = KitUnidade.query.filter_by(escola=user.escola).all()
    else:
        acesso = escolas_do_usuario(user)
        unidades = (KitUnidade.query.filter(KitUnidade.escola.in_(acesso)).all()
                    if acesso else KitUnidade.query.all())
    limite_naive = datetime.now() - timedelta(days=dias)
    nunca, atrasados, ok = [], [], []
    for u in unidades:
        uc = u.ultima_conferencia
        if not uc:
            nunca.append(u)
        else:
            dt = uc.data_conferencia
            dt_naive = dt.replace(tzinfo=None) if dt.tzinfo else dt
            if dt_naive < limite_naive:
                atrasados.append({'unidade': u, 'dias': (datetime.now() - dt_naive).days})
            else:
                ok.append(u)
    atrasados.sort(key=lambda x: x['dias'], reverse=True)
    return render_template('pendentes.html', nunca=nunca, atrasados=atrasados,
                           ok=ok, dias=dias, total=len(unidades))


# ─────────────────────────────────────────────
#  API TOKENS (admin)
# ─────────────────────────────────────────────
@app.route('/admin/api-tokens')
@login_required(roles='admin')
def api_tokens():
    tokens = APIToken.query.order_by(APIToken.criado_em.desc()).all()
    escolas_lista = Escola.query.filter_by(ativo=True).order_by(Escola.nome).all()
    return render_template('admin/api_tokens.html', tokens=tokens, escolas_lista=escolas_lista)


@app.route('/admin/api-tokens/novo', methods=['POST'])
@login_required(roles='admin')
def novo_api_token():
    nome = request.form.get('nome', '').strip()
    escola = request.form.get('escola', '').strip() or None
    if not nome:
        flash('Nome é obrigatório.', 'danger')
        return redirect(url_for('api_tokens'))
    token_val = secrets.token_hex(32)
    db.session.add(APIToken(nome=nome, token=token_val, escola=escola))
    db.session.commit()
    flash(f'Token criado! Copie agora: {token_val}', 'success')
    return redirect(url_for('api_tokens'))


@app.route('/admin/api-tokens/<int:tid>/revogar', methods=['POST'])
@login_required(roles='admin')
def revogar_token(tid):
    t = APIToken.query.get_or_404(tid)
    t.ativo = False
    db.session.commit()
    flash(f'Token "{t.nome}" revogado.', 'info')
    return redirect(url_for('api_tokens'))


@app.route('/admin/api-tokens/<int:tid>/deletar', methods=['POST'])
@login_required(roles='admin')
def deletar_token(tid):
    t = APIToken.query.get_or_404(tid)
    db.session.delete(t)
    db.session.commit()
    flash('Token removido.', 'info')
    return redirect(url_for('api_tokens'))


# ─────────────────────────────────────────────
#  API PÚBLICA (Bearer token)
# ─────────────────────────────────────────────
def _verificar_token():
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '): return None
    t = APIToken.query.filter_by(token=auth[7:].strip(), ativo=True).first()
    if t:
        t.ultimo_uso = datetime.now(timezone.utc)
        db.session.commit()
    return t


def _api_auth():
    t = _verificar_token()
    if not t:
        return None, (jsonify({'erro': 'Token inválido ou ausente.',
                               'dica': 'Envie: Authorization: Bearer SEU_TOKEN'}), 401)
    return t, None


@app.route('/api/v1/status')
def api_status():
    return jsonify({'status': 'ok', 'versao': '1.0', 'timestamp': datetime.now().isoformat()})


@app.route('/api/v1/escolas')
def api_v1_escolas():
    token, err = _api_auth()
    if err: return err
    escolas = Escola.query.filter_by(ativo=True).all()
    if token.escola: escolas = [e for e in escolas if e.nome == token.escola]
    return jsonify([{'id': e.id, 'nome': e.nome, 'cidade': e.cidade,
                     'responsavel': e.responsavel, 'total_kits': e.total_kits,
                     'kits_completos': e.kits_completos, 'saude_media': e.saude_media}
                    for e in escolas])


@app.route('/api/v1/kits')
def api_v1_kits():
    token, err = _api_auth()
    if err: return err
    qry = KitUnidade.query
    if token.escola: qry = qry.filter_by(escola=token.escola)
    elif request.args.get('escola'): qry = qry.filter_by(escola=request.args['escola'])
    if request.args.get('status'): qry = qry.filter_by(status_atual=request.args['status'])
    kits = qry.order_by(KitUnidade.escola, KitUnidade.identificador).all()
    return jsonify([{
        'id': u.id, 'identificador': u.identificador,
        'modelo': u.modelo.nome if u.modelo else None,
        'escola': u.escola, 'status': u.status_atual,
        'saude_percentual': u.saude_percentual,
        'ultima_conferencia': u.ultima_conferencia.data_conferencia.isoformat()
            if u.ultima_conferencia else None,
        'responsavel_ultima': u.ultima_conferencia.responsavel if u.ultima_conferencia else None,
    } for u in kits])


@app.route('/api/v1/kits/<int:kit_id>')
def api_v1_kit_detalhe(kit_id):
    token, err = _api_auth()
    if err: return err
    u = KitUnidade.query.get_or_404(kit_id)
    if token.escola and u.escola != token.escola:
        return jsonify({'erro': 'Acesso negado.'}), 403
    conferencias = (Conferencia.query.filter_by(kit_unidade_id=kit_id)
                    .order_by(Conferencia.data_conferencia.desc()).limit(10).all())
    return jsonify({
        'id': u.id, 'identificador': u.identificador,
        'modelo': u.modelo.nome if u.modelo else None,
        'escola': u.escola, 'status': u.status_atual, 'saude_percentual': u.saude_percentual,
        'conferencias': [{
            'id': c.id, 'data': c.data_conferencia.isoformat(),
            'responsavel': c.responsavel, 'status': c.status_resultado,
            'observacoes': c.observacoes,
            'saude': (round(
                sum(d.quantidade_encontrada for d in c.detalhes) /
                sum(d.quantidade_esperada_na_epoca for d in c.detalhes) * 100, 1)
                if c.detalhes and sum(d.quantidade_esperada_na_epoca for d in c.detalhes) > 0
                else None),
            'pecas': [{'nome': d.peca.nome if d.peca else None,
                        'codigo': d.peca.codigo_lego if d.peca else None,
                        'esperado': d.quantidade_esperada_na_epoca,
                        'encontrado': d.quantidade_encontrada,
                        'diferenca': d.quantidade_encontrada - d.quantidade_esperada_na_epoca,
                        'observacao': d.observacao_peca} for d in c.detalhes],
        } for c in conferencias],
    })


@app.route('/api/v1/perdas')
def api_v1_perdas():
    token, err = _api_auth()
    if err: return err
    escola_f = request.args.get('escola') or token.escola
    unidades = (KitUnidade.query.filter_by(escola=escola_f).all()
                if escola_f else KitUnidade.query.all())
    return jsonify(_calcular_ranking_perdas(unidades, top=9999))


@app.route('/api/v1/pendentes')
def api_v1_pendentes():
    token, err = _api_auth()
    if err: return err
    dias = int(request.args.get('dias', 30))
    escola_f = request.args.get('escola') or token.escola
    unidades = (KitUnidade.query.filter_by(escola=escola_f).all()
                if escola_f else KitUnidade.query.all())
    limite = datetime.now() - timedelta(days=dias)
    resultado = []
    for u in unidades:
        uc = u.ultima_conferencia
        if not uc:
            dias_atraso = None
        else:
            dt = uc.data_conferencia
            if hasattr(dt, 'tzinfo') and dt.tzinfo: dt = dt.replace(tzinfo=None)
            if dt >= limite: continue
            dias_atraso = (datetime.now() - dt).days
        resultado.append({'id': u.id, 'identificador': u.identificador,
                           'escola': u.escola, 'status': u.status_atual,
                           'ultima_conferencia': uc.data_conferencia.isoformat() if uc else None,
                           'dias_sem_conferencia': dias_atraso})
    resultado.sort(key=lambda x: (x['dias_sem_conferencia'] or 9999), reverse=True)
    return jsonify(resultado)


# ─────────────────────────────────────────────
#  HANDLERS DE ERRO
# ─────────────────────────────────────────────
@app.errorhandler(403)
def forbidden(e): return render_template('erros/403.html'), 403

@app.errorhandler(404)
def not_found(e): return render_template('erros/404.html'), 404

@app.errorhandler(413)
def too_large(e):
    flash('Arquivo muito grande. Limite: 5 MB.', 'danger')
    return redirect(request.referrer or url_for('index'))


# ─────────────────────────────────────────────
#  INICIALIZAÇÃO
# ─────────────────────────────────────────────
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not Usuario.query.filter_by(username='admin').first():
            admin = Usuario(username='admin', role='admin', escola='Todas')
            admin.set_password(os.environ.get('ADMIN_PASSWORD', 'Troque@isso123'))
            db.session.add(admin)
            db.session.commit()
            logger.info("Admin padrão criado. TROQUE A SENHA IMEDIATAMENTE.")
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=5000, debug=debug)
