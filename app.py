from flask import Flask, send_from_directory, render_template, g, request, redirect, url_for, flash, send_file, make_response
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash
from models import db, Configuracao, Financeiro, Lancamento, SaldoFinal, Usuario, Socio, Mensalidade, AciValorAno, AciPagamento, SuporteMensagem, AssinaturaRelatorio
from datetime import datetime
import os, sqlite3, locale
from io import BytesIO
from fpdf import FPDF
from PIL import Image
from sqlalchemy import func, extract, event, tuple_
from pdf2image import convert_from_path
from werkzeug.utils import secure_filename
import random
import re
import requests
from dotenv import load_dotenv
from collections import defaultdict
from sqlalchemy.orm import aliased, joinedload
from datetime import datetime, date
from formatador import formatar_moeda
import uuid
import math
from flask_compress import Compress
from calendar import monthrange
import threading
from urllib.parse import quote_plus
from supabase import create_client
from hashlib import sha256
from dateutil.relativedelta import relativedelta




app = Flask(__name__)
app.jinja_env.filters['formatar_moeda'] = formatar_moeda
app.secret_key = 'chave_secreta_ump_financeiro'
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', f'postgresql://postgres:yDedfkFVlIXWlgyuJtkWaWMaPRWvNodL@shinkansen.proxy.rlwy.net:11220/railway')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
Compress(app)

UPLOAD_FOLDER = os.path.join('/mnt/data', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)  # Garante que o diretório existe

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

RELATORIOS_FOLDER = os.path.join('/mnt/data', 'relatorios')
os.makedirs(RELATORIOS_FOLDER, exist_ok=True)  # Garante que a pasta exista

app.config['RELATORIOS_FOLDER'] = RELATORIOS_FOLDER


db.init_app(app)

# Configuração do Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"  # Quando não logado, irá redirecionar para a página de login


# Criação do banco de dados
with app.app_context():
    db.create_all()

@app.route('/relatorios/<filename>')
def serve_relatorio(filename):
    return send_from_directory(app.config['RELATORIOS_FOLDER'], filename)


@app.after_request
def add_header(response):
    response.cache_control.max_age = 300  # Cache de 5 minutos para estáticos
    return response


# Carregamento do usuário (necessário para Flask-Login)
@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))

# Rota de login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        senha = request.form['senha']

        usuario = Usuario.query.filter_by(username=username).first()

        if usuario and usuario.senha == senha:  
            login_user(usuario)
            atualizar_socios_usuario()
            return redirect(url_for('index'))  # Redireciona para a página principal após login bem-sucedido
        

        flash('Credenciais inválidas', 'danger')  

    return render_template('login.html')


@app.route('/logout')
@login_required  # Garante que apenas usuários logados possam acessar esta rota
def logout():
    logout_user()  # Finaliza a sessão do usuário
    return redirect(url_for('login'))  # Redireciona para a página de login    



@app.route('/alterar_senha', methods=['GET', 'POST'])
@login_required
def alterar_senha():
    if request.method == 'POST':
        senha_atual = request.form.get('senha_atual')
        nova_senha = request.form.get('nova_senha')
        confirmar_senha = request.form.get('confirmar_senha')

        # Verifica se a senha atual está correta
        if not current_user.verificar_senha(senha_atual):
            flash('Senha atual incorreta!', 'danger')
            return redirect(url_for('alterar_senha'))

        # Verifica se as novas senhas coincidem
        if nova_senha != confirmar_senha:
            flash('As novas senhas não coincidem!', 'danger')
            return redirect(url_for('alterar_senha'))

        # Atualiza a senha do usuário
        current_user.set_senha(nova_senha)
        db.session.commit()

        flash('Senha alterada com sucesso!', 'success')
        return redirect(url_for('alterar_senha'))

    return render_template('alterar_senha.html')


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.template_filter('number_format')
def number_format(value, decimal_places=2, decimal_separator='.', thousand_separator=','):
    try:
        value = float(value)
        # Formatar com base nos parâmetros
        return f"{value:,.{decimal_places}f}".replace(",", "X").replace(".", decimal_separator).replace("X", thousand_separator)
    except (ValueError, TypeError):
        return value  # Se não for um número válido, retorna o valor original  

# Definindo o filtro format_currency
@app.template_filter('format_currency')
# A função para formatar como moeda, com conversão de tipo para float
def format_currency(value):
    try:
        # Convertendo para float antes de aplicar abs()
        value = float(value)
        return locale.currency(value, grouping=True)
    except ValueError:
        return value  # Caso não seja um número válido, retorna o valor original


@app.teardown_appcontext
def close_db(exception):
    db.session.remove()

@app.route('/base')
@login_required
def base():
    return render_template('base.html')    


# Configura o locale para moeda brasileira


@app.route('/')
@login_required
def index():
    config = db.session.execute(db.select(Configuracao).filter_by(id_usuario=current_user.id)).scalars().first()

    if not config:
        config = Configuracao(
            ump_federacao="UMP Local",
            federacao_sinodo="Sinodal Exemplo",
            ano_vigente=2025,
            saldo_inicial=0  # Adicionando um valor inicial para evitar erro ao acessar depois
        )
        db.session.add(config)
        db.session.commit()

    # Somar valores por categoria na tabela "lancamento"
    outras_receitas = db.session.query(db.func.sum(Lancamento.valor)).filter(Lancamento.tipo == "Outras Receitas", Lancamento.id_usuario == current_user.id).scalar() or 0
    aci_recebida = db.session.query(db.func.sum(Lancamento.valor)).filter(Lancamento.tipo == "ACI Recebida", Lancamento.id_usuario == current_user.id).scalar() or 0
    outras_despesas = db.session.query(db.func.sum(Lancamento.valor)).filter(Lancamento.tipo == "Outras Despesas", Lancamento.id_usuario == current_user.id).scalar() or 0
    aci_enviada = db.session.query(db.func.sum(Lancamento.valor)).filter(Lancamento.tipo == "ACI Enviada", Lancamento.id_usuario == current_user.id).scalar() or 0

    # Totais de receitas e despesas
    receitas = outras_receitas + aci_recebida
    despesas = outras_despesas + aci_enviada
    saldo_final = (config.saldo_inicial or 0) + receitas - despesas

    # Formatar valores para exibição
    saldo_formatado = formatar_moeda(config.saldo_inicial or 0)
    receitas_formatadas = formatar_moeda(receitas)
    despesas_formatadas = formatar_moeda(despesas)
    saldo_final_formatado = formatar_moeda(saldo_final)
    outras_receitas_formatadas = formatar_moeda(outras_receitas)
    aci_recebida_formatada = formatar_moeda(aci_recebida)
    outras_despesas_formatadas = formatar_moeda(outras_despesas)
    aci_enviada_formatada = formatar_moeda(aci_enviada)


    response = make_response(render_template(
        'index.html',
        config=config, 
        saldo_formatado=saldo_formatado,
        receitas=receitas_formatadas,
        despesas=despesas_formatadas,
        saldo_final_formatado=saldo_final_formatado,
        outras_receitas=outras_receitas_formatadas,
        aci_recebida=aci_recebida_formatada,
        outras_despesas=outras_despesas_formatadas,
        aci_enviada=aci_enviada_formatada,
        ano=config.ano_vigente,
        now=datetime.now()
    ))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response



@app.route('/configuracoes', methods=['GET', 'POST'])
@login_required
def configuracoes():

    config = Configuracao.query.filter_by(id_usuario=current_user.id).first()  # Busca a configuração existente

    if request.method == 'POST':
        # Se não existir uma configuração, cria uma nova
        if not config:
            config = Configuracao(id_usuario=current_user.id)
            db.session.add(config)  # Adiciona ao banco apenas se for uma nova configuração

        # Adiciona o campo de e-mail ao formulário
        email = request.form['email']

        # Verifica se o e-mail já existe no banco de dados para o usuário atual
        if verificar_email_existente(email, current_user.id):
            flash('Este e-mail já está cadastrado.', 'danger')
            return render_template('configuracoes.html', config=config)

        # Atualiza os valores com os dados do formulário
        config.ump_federacao = request.form['ump_federacao']
        config.federacao_sinodo = request.form['federacao_sinodo']
        config.ano_vigente = int(request.form['ano_vigente'])  # Converte para inteiro
        config.tesoureiro_responsavel = request.form['tesoureiro_responsavel']
        config.presidente_responsavel = request.form['presidente_responsavel']
        config.email = email  # Atualiza o campo de e-mail

        # Converte o saldo para float antes de salvar no banco
        try:
            saldo_inicial = float(request.form['saldo_inicial'].replace('.', '').replace(',', '.'))
        except ValueError:
            saldo_inicial = 0.0

        # Define mês de início
        if config.sinodal == "Sim":
            try:
                config.mes_inicio_bienio = int(request.form['mes_inicio_bienio'])
            except:
                config.mes_inicio_bienio = 6  # junho padrão
        else:
            config.mes_inicio_bienio = 1  # janeiro padrão

        # Salva as mudanças no banco
        db.session.commit()

        # Garante criação e atualização dos saldos conforme o tipo de gestão
        total_meses = 27 if config.sinodal == "Sim" else 12

        saldos = SaldoFinal.query.filter_by(id_usuario=current_user.id).order_by(SaldoFinal.id).all()

        # Ajusta quantidade de linhas
        if len(saldos) < total_meses:
            for _ in range(total_meses - len(saldos)):
                db.session.add(SaldoFinal(mes=1, ano=2000, saldo=0.0, id_usuario=current_user.id))
            db.session.commit()
            saldos = SaldoFinal.query.filter_by(id_usuario=current_user.id).order_by(SaldoFinal.id).all()

        # Atualiza os saldos existentes com novos meses/anos
        mes_base = config.mes_inicio_bienio
        for i, saldo in enumerate(saldos[:total_meses]):
            mes = ((mes_base - 1 + i) % 12) + 1
            ano = config.ano_vigente + ((mes_base - 1 + i) // 12)
            saldo.mes = mes
            saldo.ano = ano

        db.session.commit()

        propagar_recalculo_a_partir(mes_inicial=config.mes_inicio_bienio, ano_base=config.ano_vigente)

        flash('Configurações salvas com sucesso!', 'success')
        return redirect(url_for('configuracoes'))  # Redireciona para evitar reenvio do formulário

    # Formatar saldo_inicial para exibição no template
    saldo_formatado = formatar_moeda(config.saldo_inicial) if config else 'R$ 0,00'

    return render_template('configuracoes.html', config=config, saldo_formatado=saldo_formatado)


# Função para verificar se o e-mail já está registrado
def verificar_email_existente(email, id_usuario):
    # Verifica se o e-mail já existe em outra configuração, mas não para o mesmo usuário
    config_existente = Configuracao.query.filter_by(email=email).first()
    if config_existente and config_existente.id_usuario != id_usuario:
        return True  # E-mail já registrado para outro usuário
    return False



def obter_saldo_inicial(mes, ano):
    config = Configuracao.query.filter_by(id_usuario=current_user.id).first()
    if not config:
        return 0.0

    mes_inicio = config.mes_inicio_bienio if config.sinodal == "Sim" else 1
    ano_inicio = config.ano_vigente

    if mes == mes_inicio and ano == ano_inicio:
        return config.saldo_inicial or 0.0

    mes_anterior = mes - 1
    ano_anterior = ano
    if mes_anterior == 0:
        mes_anterior = 12
        ano_anterior -= 1

    saldo_anterior = db.session.query(SaldoFinal.saldo).filter_by(
        mes=mes_anterior,
        ano=ano_anterior,
        id_usuario=current_user.id
    ).scalar()

    return saldo_anterior or 0.0 




def calcular_saldo_final(mes, ano, saldo_inicial):
    entradas = db.session.query(db.func.sum(Lancamento.valor)).filter(
        (Lancamento.tipo == 'Outras Receitas') | (Lancamento.tipo == 'ACI Recebida'),
        db.extract('month', Lancamento.data) == mes,
        db.extract('year', Lancamento.data) == ano,
        Lancamento.id_usuario == current_user.id 
    ).scalar() or 0

    saidas = db.session.query(db.func.sum(Lancamento.valor)).filter(
        (Lancamento.tipo == 'Outras Despesas') | (Lancamento.tipo == 'ACI Enviada'),
        db.extract('month', Lancamento.data) == mes,
        db.extract('year', Lancamento.data) == ano,
        Lancamento.id_usuario == current_user.id  
    ).scalar() or 0

    saldo_final = saldo_inicial + entradas - saidas
    return saldo_final



def salvar_saldo_final(mes, ano, saldo_inicial):
    saldo_final = calcular_saldo_final(mes, ano, saldo_inicial)

    saldo_existente = db.session.query(SaldoFinal).filter(
        SaldoFinal.mes == mes,
        SaldoFinal.ano == ano,
        SaldoFinal.id_usuario == current_user.id 
    ).first()

    if saldo_existente:
        saldo_existente.saldo = saldo_final
    else:
        saldo_novo = SaldoFinal(
            mes=mes,
            ano=ano,
            saldo=saldo_final,
            id_usuario=current_user.id  
        )
        db.session.add(saldo_novo)

    db.session.commit()



def atualizar_saldos_iniciais():
    saldo_inicial = db.session.query(Configuracao.saldo_inicial).filter_by(id_usuario=current_user.id).first()
    if saldo_inicial:
        saldo_inicial = saldo_inicial[0]
    else:
        saldo_inicial = 0  

    for mes in range(1, 13):  
        saldo_existente = db.session.query(SaldoFinal).filter(
            SaldoFinal.mes == mes,
            SaldoFinal.id_usuario == current_user.id
        ).first()

        if saldo_existente:
            saldo_existente.saldo = saldo_inicial
        else:
            saldo_novo = SaldoFinal(
                mes=mes,
                ano=2025,  
                saldo=saldo_inicial,
                id_usuario=current_user.id  
            )
            db.session.add(saldo_novo)

    db.session.commit()



def recalcular_saldos_finais():
    meses_anos = db.session.query(SaldoFinal.mes, SaldoFinal.ano).filter(
        SaldoFinal.id_usuario == current_user.id
    ).distinct().all()

    for mes, ano in meses_anos:
        saldo_inicial = obter_saldo_inicial(mes, ano)

        saldo_final = calcular_saldo_final(mes, ano, saldo_inicial)

        saldo_existente = db.session.query(SaldoFinal).filter(
            SaldoFinal.mes == mes,
            SaldoFinal.ano == ano,
            SaldoFinal.id_usuario == current_user.id
        ).first()

        if saldo_existente:
            saldo_existente.saldo = saldo_final
        else:
            saldo_novo = SaldoFinal(
                mes=mes,
                ano=ano,
                saldo=saldo_final,
                id_usuario=current_user.id  
            )
            db.session.add(saldo_novo)

    db.session.commit()

def atualizar_saldo_final_ao_lancar(mes, ano):
    saldo_inicial = obter_saldo_inicial(mes, ano)
    saldo_final = calcular_saldo_final(mes, ano, saldo_inicial)

    saldo_existente = db.session.query(SaldoFinal).filter_by(
        mes=mes,
        ano=ano,
        id_usuario=current_user.id
    ).first()

    if saldo_existente:
        saldo_existente.saldo = saldo_final
    else:
        saldo_novo = SaldoFinal(
            mes=mes,
            ano=ano,
            saldo=saldo_final,
            id_usuario=current_user.id
        )
        db.session.add(saldo_novo)

    db.session.commit()

def propagar_recalculo_a_partir(mes_inicial, ano_base):
    config = Configuracao.query.filter_by(id_usuario=current_user.id).first()
    if not config:
        return

    total_meses = 27 if config.sinodal == "Sim" else 12
    saldo = obter_saldo_inicial(mes_inicial, ano_base)

    for i in range(total_meses):
        mes_atual = ((mes_inicial - 1 + i) % 12) + 1
        ano_atual = ano_base + ((mes_inicial - 1 + i) // 12)

        saldo_final = calcular_saldo_final(mes_atual, ano_atual, saldo)

        saldo_existente = SaldoFinal.query.filter_by(
            mes=mes_atual,
            ano=ano_atual,
            id_usuario=current_user.id
        ).first()

        if saldo_existente:
            saldo_existente.saldo = saldo_final
        else:
            novo = SaldoFinal(
                mes=mes_atual,
                ano=ano_atual,
                saldo=saldo_final,
                id_usuario=current_user.id
            )
            db.session.add(novo)

        saldo = saldo_final

    db.session.commit()


@app.route('/mes/<int:mes>/<int:ano>')
@login_required
def mes(mes, ano):
    configuracao = Configuracao.query.filter_by(id_usuario=current_user.id).first()

    if not configuracao:
        flash("Configuração do usuário não encontrada.", "warning")
        return redirect(url_for('index'))

    # Define se o usuário é sinodal
    sinodal = configuracao.sinodal == "Sim"
    ano_vigente = configuracao.ano_vigente
    mes_inicio = configuracao.mes_inicio_bienio if sinodal else 1
    total_meses = 27 if sinodal else 12

    # Gera os pares (mes, ano) válidos para navegação
    meses_validos = []
    for i in range(total_meses):
        m = ((mes_inicio - 1 + i) % 12) + 1
        a = ano_vigente + ((mes_inicio - 1 + i) // 12)
        meses_validos.append((m, a))

    # Verifica se o mês/ano solicitado está dentro dos permitidos
    if (mes, ano) not in meses_validos:
        flash("Mês fora do intervalo da gestão configurada.", "danger")
        return redirect(url_for('index'))

    # Obtem saldo inicial
    saldo_inicial = obter_saldo_inicial(mes, ano)

    # Consulta os lançamentos do mês
    lancamentos = Lancamento.query.filter(
        db.extract('year', Lancamento.data) == ano,
        db.extract('month', Lancamento.data) == mes,
        Lancamento.id_usuario == current_user.id
    ).order_by(Lancamento.data.asc(), Lancamento.id.asc()).all()

    # Cálculo das somas
    entradas = sum(l.valor for l in lancamentos if l.tipo in ['Outras Receitas', 'ACI Recebida'])
    saidas = sum(l.valor for l in lancamentos if l.tipo in ['Outras Despesas', 'ACI Enviada'])

    saldo = saldo_inicial + entradas - saidas

    # Formatação
    saldo_inicial_formatado = formatar_moeda(saldo_inicial)
    entradas_formatado = formatar_moeda(entradas)
    saidas_formatado = formatar_moeda(saidas)
    saldo_formatado = formatar_moeda(saldo)
    mes_formatado = str(mes).zfill(2)

    response = make_response(render_template(
        'mes.html',
        mes=mes,
        mes_formatado=mes_formatado,
        ano=ano,
        saldo_inicial=saldo_inicial_formatado,
        entradas=entradas_formatado,
        saidas=saidas_formatado,
        saldo=saldo_formatado,
        lancamentos=lancamentos,
        ano_vigente=ano_vigente,
        meses_disponiveis=meses_validos
    ))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


@app.route('/lancamentos')
@login_required
def lancamentos():
    config = Configuracao.query.filter_by(id_usuario=current_user.id).first()
    if not config:
        flash("Configuração do usuário não encontrada.", "warning")
        return redirect(url_for('index'))

    sinodal = config.sinodal == "Sim"
    mes_inicio = config.mes_inicio_bienio if sinodal else 1
    total_meses = 27 if sinodal else 12
    ano_base = config.ano_vigente

    meses_disponiveis = []
    for i in range(total_meses):
        m = ((mes_inicio - 1 + i) % 12) + 1
        a = ano_base + ((mes_inicio - 1 + i) // 12)
        meses_disponiveis.append((m, a))

    return render_template('lancamentos.html', meses_disponiveis=meses_disponiveis)

def reprocessar_png_para_nao_interlaced(path_original):
    try:
        with Image.open(path_original) as img:
            img = img.convert("RGB")  # Garante compatibilidade
            img.save(path_original, format="PNG", interlace=0)
    except Exception as e:
        print(f"Erro ao reprocessar PNG: {e}")


@app.route('/adicionar_lancamento/<int:mes>/<int:ano>', methods=['GET', 'POST'])
@login_required 
def adicionar_lancamento(mes, ano):
    configuracao = Configuracao.query.filter_by(id_usuario=current_user.id).first()
    if not configuracao:
        flash("Configuração do usuário não encontrada.", "warning")
        return redirect(url_for('index'))

    sinodal = configuracao.sinodal == "Sim"
    mes_inicio = configuracao.mes_inicio_bienio if sinodal else 1
    total_meses = 27 if sinodal else 12
    ano_base = configuracao.ano_vigente

    # Gera todos os pares válidos
    meses_validos = []
    for i in range(total_meses):
        m = ((mes_inicio - 1 + i) % 12) + 1
        a = ano_base + ((mes_inicio - 1 + i) // 12)
        meses_validos.append((m, a))

    # Verifica se o mês/ano informado está dentro da gestão
    if (mes, ano) not in meses_validos:
        flash("Mês fora do intervalo da gestão configurada.", "danger")
        return redirect(url_for('lancamentos'))  

    if request.method == 'POST':
        data = request.form.get('data')
        tipo = request.form.get('tipo')
        descricao = request.form.get('descricao')
        valor = request.form.get('valor')
        comprovante = None

        if not data or not tipo or not descricao or not valor:
            flash("Erro: Todos os campos devem ser preenchidos.", "danger")
            return redirect(url_for('adicionar_lancamento', mes=mes, ano=ano))

        try:
            data = datetime.strptime(data, '%Y-%m-%d').date()
            valor = float(valor)
        except ValueError:
            flash("Erro: Data ou valor inválidos.", "danger")
            return redirect(url_for('adicionar_lancamento', mes=mes, ano=ano))

        if (data.month, data.year) not in meses_validos:
            flash("Erro: A data informada está fora do intervalo da gestão.", "danger")
            return redirect(url_for('adicionar_lancamento', mes=mes, ano=ano))

        if 'comprovante' in request.files:
            file = request.files['comprovante']
        
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
        
                ext = os.path.splitext(filename)[1].lower()
                base_name = os.path.splitext(filename)[0]
        
                while True:
                    unique_id = uuid.uuid4().hex[:8]
                    new_filename = f"{base_name}_{unique_id}{ext}"
                    existing = Lancamento.query.filter_by(
                        comprovante=os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
                    ).first()
                    if not existing:
                        break
        
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
                file.save(file_path)
        
                # Reprocessa PNGs para evitar erro de entrelaçamento no FPDF
                if ext == '.png':
                    reprocessar_png_para_nao_interlaced(file_path)
        
                # Converte PDF para JPG (mantendo seu comportamento atual)
                if ext == '.pdf':
                    from pdf2image import convert_from_path
                    images = convert_from_path(file_path)
        
                    if images:
                        image_filename = new_filename.replace('.pdf', '.jpg')
                        image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
        
                        images[0].save(image_path, 'JPEG')
                        os.remove(file_path)
        
                        new_filename = image_filename
                        file_path = image_path
        
                comprovante = new_filename  

        ultimo_id_lanc = db.session.query(func.max(Lancamento.id_lancamento))\
            .filter_by(id_usuario=current_user.id).scalar()
        proximo_id_lanc = (ultimo_id_lanc or 0) + 1

        lancamento = Lancamento(
            data=data,
            tipo=tipo,
            descricao=descricao,
            valor=valor,
            comprovante=comprovante,
            id_usuario=current_user.id,
            id_lancamento=proximo_id_lanc
        )
        db.session.add(lancamento)
        db.session.commit()

        todos_lancamentos = Lancamento.query.filter_by(id_usuario=current_user.id).order_by(Lancamento.data.asc(), Lancamento.id.asc()).all()

        for index, lanc in enumerate(todos_lancamentos, start=1):
            lanc.id_lancamento = index

        db.session.commit()

        propagar_recalculo_a_partir(data.month, data.year)

        flash("Lançamento adicionado com sucesso!", "success")

        return redirect(url_for('mes', mes=data.month, ano=data.year))

    return render_template('adicionar_lancamento.html', mes=mes, ano=ano)



@app.route('/mes/<int:mes>/<int:ano>', methods=['GET'])
@login_required  # Garante que apenas usuários logados acessem essa rota
def visualizar_mes(mes, ano):
    # Recupera os lançamentos do mês e ano para o usuário logado
    lancamentos = Lancamento.query.filter(
        db.extract('month', Lancamento.data) == mes,
        db.extract('year', Lancamento.data) == ano,
        Lancamento.id_usuario == current_user.id
    ).all()

    # Cálculos de saldo e totais (entradas e saídas)
    entradas = sum([lancamento.valor for lancamento in lancamentos if lancamento.tipo == 'Entrada'])
    saidas = sum([lancamento.valor for lancamento in lancamentos if lancamento.tipo == 'Saída'])

    # Obter o saldo inicial do mês para o usuário logado (usando a função ajustada)
    saldo_inicial = obter_saldo_inicial(mes, ano)

    # Calcular o saldo final
    saldo = saldo_inicial + entradas - saidas

    return render_template(
        'mes.html',
        lancamentos=lancamentos,
        mes=mes,
        ano=ano,
        entradas=entradas,
        saidas=saidas,
        saldo=saldo,
        saldo_inicial=saldo_inicial
    )

@app.route('/uploads/<filename>')
def serve_file(filename):
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.isfile(file_path):
        abort(404)
    return send_file(file_path)


import requests

@app.route('/excluir_lancamento/<int:id>', methods=['POST'])
@login_required  
def excluir_lancamento(id):
    mes = request.form.get('mes')
    ano = request.form.get('ano')

    if not mes or not ano:
        flash('Erro: Mês ou ano não informado.', 'danger')
        config = Configuracao.query.filter_by(id_usuario=current_user.id).first()
        ano_base = config.ano_vigente if config else datetime.now().year
        return redirect(url_for('mes', mes=1, ano=ano_base))

    mes = int(mes)  
    ano = int(ano)

    configuracao = Configuracao.query.filter_by(id_usuario=current_user.id).first()
    if not configuracao:
        flash("Configuração do usuário não encontrada.", "warning")
        return redirect(url_for('index'))

    sinodal = configuracao.sinodal == "Sim"
    mes_inicio = configuracao.mes_inicio_bienio if sinodal else 1
    total_meses = 27 if sinodal else 12
    ano_base = configuracao.ano_vigente

    # Gera os meses válidos da gestão
    meses_validos = []
    for i in range(total_meses):
        m = ((mes_inicio - 1 + i) % 12) + 1
        a = ano_base + ((mes_inicio - 1 + i) // 12)
        meses_validos.append((m, a))

    if (mes, ano) not in meses_validos:
        flash("Erro: Mês fora do intervalo da gestão configurada.", "danger")
        return redirect(url_for('mes', mes=mes, ano=ano))  

    with app.app_context():
        lancamento = Lancamento.query.filter_by(
            id=id,
            id_usuario=current_user.id
        ).first()

        if lancamento:
            if lancamento.comprovante:
                comprovante_path = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(lancamento.comprovante))
            
                if os.path.exists(comprovante_path):
                    os.remove(comprovante_path)
                    print(f"Comprovante excluído: {comprovante_path}")
                else:
                    print(f"Arquivo não encontrado: {comprovante_path}")


            db.session.delete(lancamento)
            db.session.commit()

            # Reorganiza os códigos após exclusão
            todos_lancamentos = Lancamento.query.filter_by(id_usuario=current_user.id).order_by(Lancamento.data.asc(), Lancamento.id.asc()).all()

            for index, lanc in enumerate(todos_lancamentos, start=1):
                lanc.id_lancamento = index
    
            db.session.commit()

            propagar_recalculo_a_partir(mes, ano)

            flash('Lançamento e comprovante excluídos com sucesso!', 'success')
        else:
            flash('Lançamento não encontrado ou você não tem permissão para excluí-lo!', 'danger')
            return redirect(url_for('mes', mes=mes, ano=ano))

    return redirect(url_for('mes', mes=mes, ano=ano))


@app.route('/editar_lancamento/<int:id>', methods=['GET', 'POST'])
@login_required  # Garante que apenas usuários logados acessem essa rota
def editar_lancamento(id):
    # Busca o lançamento apenas se pertencer ao usuário logado
    lancamento = Lancamento.query.filter_by(
        id=id,
        id_usuario=current_user.id
    ).first()

    if not lancamento:
        flash("Lançamento não encontrado ou você não tem permissão para editá-lo!", "danger")
        return redirect(url_for('lancamentos'))  # Redireciona se não encontrado ou sem permissão

    # Recupera os parâmetros 'mes' e 'ano' diretamente da URL
    mes = request.args.get('mes')
    ano = request.args.get('ano')

    if not mes or not ano:
        flash("Erro: Mês ou ano não informado.", "danger")
        config = Configuracao.query.filter_by(id_usuario=current_user.id).first()
        ano_base = config.ano_vigente if config else datetime.now().year
        return redirect(url_for('mes', mes=1, ano=ano_base)) 

    mes = int(mes)
    ano = int(ano)

    # Busca configuração do usuário para validar meses
    configuracao = Configuracao.query.filter_by(id_usuario=current_user.id).first()
    if not configuracao:
        flash("Configuração do usuário não encontrada.", "warning")
        return redirect(url_for('index'))

    sinodal = configuracao.sinodal == "Sim"
    mes_inicio = configuracao.mes_inicio_bienio if sinodal else 1
    total_meses = 27 if sinodal else 12
    ano_base = configuracao.ano_vigente

    meses_validos = []
    for i in range(total_meses):
        m = ((mes_inicio - 1 + i) % 12) + 1
        a = ano_base + ((mes_inicio - 1 + i) // 12)
        meses_validos.append((m, a))

    if (mes, ano) not in meses_validos:
        flash("Mês fora do intervalo da gestão configurada.", "danger")
        return redirect(url_for('mes', mes=mes, ano=ano))  # Redireciona para um padrão se faltar dados

    if request.method == 'POST':
        data_string = request.form['data']
        try:
            nova_data = datetime.strptime(data_string, '%Y-%m-%d').date()
        except ValueError:
            flash("Data inválida.", "danger")
            return redirect(url_for('editar_lancamento', id=id, mes=mes, ano=ano))

        # Verifica se nova data está dentro dos meses válidos
        if (nova_data.month, nova_data.year) not in meses_validos:
            flash("Erro: A data informada está fora do intervalo da gestão.", "danger")
            return redirect(url_for('editar_lancamento', id=id, mes=mes, ano=ano))

        lancamento.data = nova_data
        lancamento.tipo = request.form['tipo']
        lancamento.descricao = request.form['descricao']

        try:
            lancamento.valor = float(request.form['valor'])
        except ValueError:
            flash("Valor inválido.", "danger")
            return redirect(url_for('editar_lancamento', id=id, mes=mes, ano=ano))

        if 'comprovante' in request.files:
            file = request.files['comprovante']
        
            if file and allowed_file(file.filename):
                if lancamento.comprovante:
                comprovante_path = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(lancamento.comprovante))
            
                if os.path.exists(comprovante_path):
                    os.remove(comprovante_path)
                    print(f"Comprovante excluído: {comprovante_path}")
                else:
                    print(f"Arquivo não encontrado: {comprovante_path}")
                    
                filename = secure_filename(file.filename)
                ext = os.path.splitext(filename)[1].lower()
                base_name = os.path.splitext(filename)[0]
        
                while True:
                    unique_id = uuid.uuid4().hex[:8]
                    new_filename = f"{base_name}_{unique_id}{ext}"
                    existing = Lancamento.query.filter_by(
                        comprovante=os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
                    ).first()
                    if not existing:
                        break
        
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
                file.save(file_path)
        
                # Reprocessa PNGs para evitar erro de entrelaçamento no FPDF
                if ext == '.png':
                    reprocessar_png_para_nao_interlaced(file_path)
        
                # Converte PDF para JPG (mantendo seu comportamento atual)
                if ext == '.pdf':
                    from pdf2image import convert_from_path
                    images = convert_from_path(file_path)
        
                    if images:
                        image_filename = new_filename.replace('.pdf', '.jpg')
                        image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
        
                        images[0].save(image_path, 'JPEG')
                        os.remove(file_path)
        
                        new_filename = image_filename
                        file_path = image_path
        
                lancamento.comprovante = new_filename

        db.session.commit()  # Salva as alterações no banco

        todos_lancamentos = Lancamento.query.filter_by(id_usuario=current_user.id).order_by(Lancamento.data.asc(), Lancamento.id.asc()).all()

        for index, lanc in enumerate(todos_lancamentos, start=1):
            lanc.id_lancamento = index

        db.session.commit()
        
        propagar_recalculo_a_partir(nova_data.month, nova_data.year)

        flash("Lançamento atualizado com sucesso!", "success")
        # Redireciona para a página do mês, passando 'mes' e 'ano' como parâmetros
        return redirect(url_for('mes', mes=mes, ano=ano))

    return render_template('editar_lancamento.html', lancamento=lancamento, mes=mes, ano=ano)





def dados_relatorio(mes=None):
    dados = []
    saldo_anterior = 0

    configuracao = Configuracao.query.filter_by(id_usuario=current_user.id).first()
    if not configuracao:
        flash('Configuração não encontrada.', 'danger')
        return []

    ano_vigente = configuracao.ano_vigente
    sinodal = configuracao.sinodal == 'Sim'
    mes_inicio = configuracao.mes_inicio_bienio or 1

    # Calcula datas para o intervalo da gestão
    if sinodal:
        data_inicio = datetime(ano_vigente, mes_inicio, 1)
        data_fim = (data_inicio + relativedelta(months=27)) - timedelta(days=1)
        meses_range = [(data_inicio + relativedelta(months=i)).month for i in range(27)]
        anos_range = [(data_inicio + relativedelta(months=i)).year for i in range(27)]
        datas_validas = [(m, a) for m, a in zip(meses_range, anos_range)]
    else:
        data_inicio = datetime(ano_vigente, 1, 1)
        data_fim = datetime(ano_vigente, 12, 31)
        datas_validas = [(m, ano_vigente) for m in range(1, 13)]

    # Somatórios gerais
    outras_receitas = float(db.session.query(func.sum(Lancamento.valor)).filter(
        Lancamento.tipo == 'Outras Receitas',
        tuple_(
            extract('month', Lancamento.data),
            extract('year', Lancamento.data)
        ).in_(datas_validas),
        Lancamento.id_usuario == current_user.id
    ).scalar() or 0)

    aci_recebida = float(db.session.query(func.sum(Lancamento.valor)).filter(
        Lancamento.tipo == 'ACI Recebida',
        tuple_(
            extract('month', Lancamento.data),
            extract('year', Lancamento.data)
        ).in_(datas_validas),
        Lancamento.id_usuario == current_user.id
    ).scalar() or 0)

    outras_despesas = float(db.session.query(func.sum(Lancamento.valor)).filter(
        Lancamento.tipo == 'Outras Despesas',
        tuple_(
            extract('month', Lancamento.data),
            extract('year', Lancamento.data)
        ).in_(datas_validas),
        Lancamento.id_usuario == current_user.id
    ).scalar() or 0)

    aci_enviada = float(db.session.query(func.sum(Lancamento.valor)).filter(
        Lancamento.tipo == 'ACI Enviada',
        tuple_(
            extract('month', Lancamento.data),
            extract('year', Lancamento.data)
        ).in_(datas_validas),
        Lancamento.id_usuario == current_user.id
    ).scalar() or 0)


    total_receitas = outras_receitas + aci_recebida
    total_despesas = outras_despesas + aci_enviada
    saldo_final_ano = (configuracao.saldo_inicial or 0) + total_receitas - total_despesas

    # Definição de quais meses serão exibidos
    if mes:
        datas_validas = [d for d in datas_validas if d[0] == mes]

    for mes_atual, ano_atual in datas_validas:
        saldo_inicial = saldo_anterior if dados else float(configuracao.saldo_inicial or 0)

        entradas = float(db.session.query(func.sum(Lancamento.valor)).filter(
            Lancamento.tipo.in_(['Outras Receitas', 'ACI Recebida']),
            extract('month', Lancamento.data) == mes_atual,
            extract('year', Lancamento.data) == ano_atual,
            Lancamento.id_usuario == current_user.id
        ).scalar() or 0)

        saidas = float(db.session.query(func.sum(Lancamento.valor)).filter(
            Lancamento.tipo.in_(['Outras Despesas', 'ACI Enviada']),
            extract('month', Lancamento.data) == mes_atual,
            extract('year', Lancamento.data) == ano_atual,
            Lancamento.id_usuario == current_user.id
        ).scalar() or 0)

        saldo_final = saldo_inicial + entradas - saidas
        saldo_anterior = saldo_final

        lancamentos = Lancamento.query.filter(
            extract('month', Lancamento.data) == mes_atual,
            extract('year', Lancamento.data) == ano_atual,
            Lancamento.id_usuario == current_user.id
        ).order_by(Lancamento.data.asc(), Lancamento.id.asc()).all()

        dados.append({
            'mes': mes_atual,
            'ano': ano_atual,
            'saldo_inicial': formatar_moeda(saldo_inicial),
            'entradas': formatar_moeda(entradas),
            'saidas': formatar_moeda(saidas),
            'saldo_final': formatar_moeda(saldo_final),
            'saldo_final_ano': formatar_moeda(saldo_final_ano),
            'lancamentos': lancamentos,
            'configuracao': configuracao,
            'outras_receitas': formatar_moeda(outras_receitas),
            'aci_recebida': formatar_moeda(aci_recebida),
            'outras_despesas': formatar_moeda(outras_despesas),
            'aci_enviada': formatar_moeda(aci_enviada),
            'total_receitas': formatar_moeda(total_receitas),
            'total_despesas': formatar_moeda(total_despesas),
            'ano_vigente': ano_vigente
        })

    return dados


@app.route('/relatorio')
@login_required  # Garante que apenas usuários logados acessem essa rota
def relatorio():
    config = Configuracao.query.filter_by(id_usuario=current_user.id).first()
    mes = request.args.get('mes', type=int, default=None)
    dados = dados_relatorio(mes)  # Agora busca o ano automaticamente da configuração do usuário
    ano = dados[0]['ano_vigente'] if dados else datetime.now().year  # Obtém o ano vigente da configuração

    response = make_response(render_template(
        'relatorio.html', 
        config=config, 
        dados=dados, 
        ano=ano, 
        mes=mes
    ))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

@app.route('/relatorio/exportar')
@login_required  # Garante que apenas usuários logados acessem essa rota
def exportar_relatorio():
    mes = request.args.get('mes', type=int, default=None)
    dados = dados_relatorio(mes)  # Agora busca o ano automaticamente da configuração do usuário
    ano = dados[0]['ano_vigente'] if dados else datetime.now().year  # Obtém o ano vigente da configuração


    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    # Se não houver dados, usar um dicionário vazio para evitar erros
    dados_config = dados[0].get('configuracao', {}) if dados else {}

    # Adicionar logo centralizada
    logo_path = os.path.join(app.static_folder, "Logos/logo_sinodal 02.png")
    try:
        pdf.image(logo_path, x=80, y=8, w=50)  # Centraliza a logo no topo
    except:
        pass  # Se a imagem não for encontrada, continua sem erro

    pdf.ln(26)  # Adiciona um espaço abaixo da logo para os títulos

    # Título do relatório centralizado
    pdf.set_text_color(28, 30, 62)  # Azul Escuro
    pdf.set_font("Arial", style='B', size=14)
    # Condicional para exibir o título corretamente
    if dados_config.sinodal == 'Sim':
        titulo = f"RELATÓRIO FINANCEIRO {ano} - {ano + 2}{f' - Mês {mes}' if mes else ''}"
    else:
        titulo = f"RELATÓRIO FINANCEIRO {ano}{f' - Mês {mes}' if mes else ''}"

    pdf.cell(190, 10, txt=titulo, ln=True, align='C')
    pdf.ln(0)

    # Título com Nome da UMP/Federação centralizado
    pdf.set_font("Arial", style='B', size=12)
    campo = f"{dados_config.ump_federacao if hasattr(dados_config, 'ump_federacao') else 'Não definido'} - {dados_config.federacao_sinodo if hasattr(dados_config, 'federacao_sinodo') else 'Não definido'}"
    pdf.cell(190, 10, campo, ln=True, align='C')
    pdf.ln(5)  # Espaço após a linha

    # === Cabeçalho ===
    pdf.set_text_color(255, 255, 255)  # Cor branca
    pdf.set_font("Arial", style='B', size=14)
    pdf.set_fill_color(28, 30, 62)  # Azul
    pdf.cell(190, 8, txt="Informações de Cabeçalho", ln=True, align='C', fill=True)
    pdf.ln(2)

    # Configuração da tabela do cabeçalho
    largura_campo = 95
    largura_valor = 95
    altura_celula = 8

    pdf.set_text_color(28, 30, 62)  # Azul Escuro
    pdf.set_font("Arial", style='B', size=11)
    pdf.set_fill_color(201, 203, 231)  # Azul Claro
    pdf.cell(largura_campo, altura_celula, "Campos", border=1, align='C', fill=True)
    pdf.cell(largura_valor, altura_celula, "Informações", border=1, align='C', fill=True)
    pdf.ln()

    pdf.set_text_color(0, 0, 0)  # Cor preta
    pdf.set_font("Arial", size=11)
    # Define os rótulos dinamicamente com base nas regras
    if hasattr(dados_config, 'sinodal') and dados_config.sinodal == 'Sim':
        label_sinodal = "Sinodal:"
        label_federacao = "Sínodo:"
        label_ativos = "Federações:"
        label_cooperadores = "UMPs:"
        valor_cooperadores = str(dados_config.socios_cooperadores if hasattr(dados_config, 'socios_cooperadores') else "Não definido")

    elif hasattr(dados_config, 'gestor') and dados_config.gestor == 'Sim':
        label_sinodal = "Federação:"
        label_federacao = "Presbitério:"
        label_ativos = "UMPs:"
        label_cooperadores = ""
        valor_cooperadores = ""  # 👉 Aqui não exibe nada

    else:
        label_sinodal = "UMP:"
        label_federacao = "Federação:"
        label_ativos = "Sócios Ativos:"
        label_cooperadores = "Sócios Cooperadores:"
        valor_cooperadores = str(dados_config.socios_cooperadores if hasattr(dados_config, 'socios_cooperadores') else "Não definido")


    # Cálculo do ano da gestão com base na condição sinodal
    if hasattr(dados_config, 'sinodal') and dados_config.sinodal == 'Sim':
        gestao = f"{dados_config.ano_vigente}/{dados_config.ano_vigente + 2}"
    else:
        gestao = str(dados_config.ano_vigente if hasattr(dados_config, 'ano_vigente') else "Não definido")

    campos = [
        (label_sinodal, dados_config.ump_federacao if hasattr(dados_config, 'ump_federacao') else "Não definido"),
        (label_federacao, dados_config.federacao_sinodo if hasattr(dados_config, 'federacao_sinodo') else "Não definido"),
        ("Gestão:", gestao),
        (label_ativos, str(dados_config.socios_ativos if hasattr(dados_config, 'socios_ativos') else "Não definido")),
        (label_cooperadores, valor_cooperadores),
    ]


    for campo, valor in campos:
        pdf.cell(largura_campo, altura_celula, campo, border=1)
        pdf.cell(largura_valor, altura_celula, valor, border=1)
        pdf.ln()

    pdf.ln(2)  # Espaço após o cabeçalho

    # === Resumo Financeiro ===
    pdf.set_text_color(255, 255, 255)  # Cor branca
    pdf.set_font("Arial", style='B', size=14)
    pdf.set_fill_color(28, 30, 62)  # Azul
    pdf.cell(190, 8, txt="Resumo Financeiro", ln=True, align='C', fill=True)
    pdf.ln(2)

    resumo = dados[0] if dados else {}

    # === Saldo Inicial ===
    pdf.set_text_color(28, 30, 62)  # Cor branca
    pdf.set_font("Arial", style='B', size=11)
    pdf.set_fill_color(180, 230, 220)  # Azul
    pdf.cell(190, 8, f"Saldo Inicial: {(resumo.get('saldo_inicial', 0.00))}", ln=True, align='C', fill=True)
    pdf.ln(2)

    pdf.set_text_color(28, 30, 62)  # Azul Escuro
    pdf.set_font("Arial", style='B', size=11)
    pdf.set_fill_color(201, 203, 231)  # Azul Claro
    pdf.cell(largura_campo, altura_celula, "Receitas", border=1, align='C', fill=True)
    pdf.cell(largura_valor, altura_celula, "Despesas", border=1, align='C', fill=True)
    pdf.ln()

    pdf.set_text_color(0, 0, 0)  # Cor preta
    pdf.set_font("Arial", size=11)
    resumo_financeiro = [
        (f"Outras Receitas: {(resumo.get('outras_receitas', 0.00))}", 
         f"Outras Despesas: {(resumo.get('outras_despesas', 0.00))}"),
        (f"ACI Recebida: {(resumo.get('aci_recebida', 0.00))}", 
         f"ACI Enviada: {(resumo.get('aci_enviada', 0.00))}"),
        (f"Total de Receitas: {(resumo.get('total_receitas', 0.00))}",
         f"Total de Despesas: {(resumo.get('total_despesas', 0.00))}")
    ]

    for receita, despesa in resumo_financeiro:
        pdf.cell(largura_campo, altura_celula, receita, border=1)
        pdf.cell(largura_valor, altura_celula, despesa, border=1)
        pdf.ln()

    pdf.ln(2)

    # === Saldo Final ===
    pdf.set_text_color(28, 30, 62)  # C
    pdf.set_font("Arial", style='B', size=11)
    pdf.set_fill_color(180, 230, 220)  # Azul
    pdf.cell(190, 8, f"Saldo Final: {(resumo.get('saldo_final_ano', 0.00))}", ln=True, align='C', fill=True)

    # Dados usados para gerar o hash único
    agora = datetime.now()
    dados_assinatura = f"{dados_config.tesoureiro_responsavel}|{dados_config.presidente_responsavel}|{ano}|{agora.isoformat()}"
    hash_assinatura = sha256(dados_assinatura.encode()).hexdigest()

    # Verifica se já existe assinatura para este usuário e ano
    assinatura_existente = AssinaturaRelatorio.query.filter_by(
        id_usuario=current_user.id,
        ano=ano,
        mes=0  # Assinatura de relatório anual
    ).first()

    if assinatura_existente:
        # Atualiza os dados existentes
        assinatura_existente.hash = hash_assinatura
        assinatura_existente.data_assinatura = agora
    else:
        # Cria novo registro
        nova_assinatura = AssinaturaRelatorio(
            hash=hash_assinatura,
            data_assinatura=agora,
            mes=0,  # indicando assinatura do relatório anual
            ano=ano,
            id_usuario=current_user.id
        )
        db.session.add(nova_assinatura)

    db.session.commit()


    # Caminho para o ícone de certificado
    cert_icon_path = os.path.join(app.static_folder, "Logos/certificado.png")

    # Espaço antes da seção
    pdf.ln(12)

    # === Bloco de assinatura Tesoureiro ===
    largura_icone = 14
    espaco_entre = 1

    # Define as linhas do bloco
    assinatura_linhas = [
        "Assinado digitalmente por:",
        f"Tesoureiro: {dados_config.tesoureiro_responsavel}",
        f"Data/Hora: {agora.strftime('%d/%m/%Y %H:%M:%S')}"
    ]

    # Fonte para medição
    pdf.set_font("Arial", size=8)

    # Medidas
    largura_texto = max(pdf.get_string_width(linha) for linha in assinatura_linhas)
    largura_total = largura_icone + espaco_entre + largura_texto

    # Ponto inicial centralizado no documento
    x_inicial = (pdf.w - largura_total) / 2
    y_inicial = pdf.get_y()

    # Se o ícone existir, desenha ele e ajusta posição do texto
    if os.path.exists(cert_icon_path):
        pdf.image(cert_icon_path, x=x_inicial, y=y_inicial, w=largura_icone, h=largura_icone)
        x_texto = x_inicial + largura_icone + espaco_entre
    else:
        # Se não existir, começa o texto no centro ajustado
        x_texto = (pdf.w - largura_texto) / 2

    # Escreve cada linha de texto ao lado (ou centralizado se sem ícone)
    y_texto = y_inicial
    for linha in assinatura_linhas:
        pdf.set_xy(x_texto, y_texto)
        pdf.cell(w=largura_texto, h=5, txt=linha, ln=0, align='L')
        y_texto += 5

    pdf.ln(12)



    # === Bloco de assinatura Presidente ===
    largura_icone = 14
    espaco_entre = 1

    # Define as linhas do bloco
    assinatura_linhas = [
        "Assinado digitalmente por:",
        f"Presidente: {dados_config.presidente_responsavel}",
        f"Data/Hora: {agora.strftime('%d/%m/%Y %H:%M:%S')}"
    ]

    # Fonte para medição
    pdf.set_font("Arial", size=8)

    # Medidas
    largura_texto = max(pdf.get_string_width(linha) for linha in assinatura_linhas)
    largura_total = largura_icone + espaco_entre + largura_texto

    # Ponto inicial centralizado no documento
    x_inicial = (pdf.w - largura_total) / 2
    y_inicial = pdf.get_y()

    # Se o ícone existir, desenha ele e ajusta posição do texto
    if os.path.exists(cert_icon_path):
        pdf.image(cert_icon_path, x=x_inicial, y=y_inicial, w=largura_icone, h=largura_icone)
        x_texto = x_inicial + largura_icone + espaco_entre
    else:
        # Se não existir, começa o texto no centro ajustado
        x_texto = (pdf.w - largura_texto) / 2

    # Escreve cada linha de texto ao lado (ou centralizado se sem ícone)
    y_texto = y_inicial
    for linha in assinatura_linhas:
        pdf.set_xy(x_texto, y_texto)
        pdf.cell(w=largura_texto, h=5, txt=linha, ln=0, align='L')
        y_texto += 5

    pdf.ln(4)


    # --- Link de validação ---
    pdf.set_text_color(0, 0, 255)
    link_validacao = f"https://web-production-2a02a.up.railway.app//validar/{hash_assinatura}"
    pdf.set_font("Arial", 'U', size=9)
    pdf.cell(190, 8, "Verifique aqui a autenticidade do relatório", ln=True, align='C', link=link_validacao)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(8)

    # Rodapé
    pdf.set_font("Arial", size=10)
    campo = f"{dados_config.ump_federacao if hasattr(dados_config, 'ump_federacao') else 'Não definido'} - {dados_config.federacao_sinodo if hasattr(dados_config, 'federacao_sinodo') else 'Não definido'}"
    pdf.cell(190, 10, campo, ln=True, align='C')
    pdf.ln(0)  # Espaço após a linha

    # Moto da UMP
    pdf.set_font("Arial", size=9)
    pdf.cell(190, 10, txt="''Alegres na Esperança, Fortes na Fé, Dedicados no Amor, Unidos no Trabalho''", ln=True, align='C')
    pdf.ln(10)  # Espaço antes do conteúdo

    # Dicionário de meses
    meses = {
        1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio", 6: "Junho",
        7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
    }

    # === Loop sobre os meses ===
    for d in dados:
        mes_nome = meses.get(d['mes'], f"Mês {d['mes']}")  # Obtém o nome do mês
        pdf.set_text_color(28, 30, 62)  # Azul Escuro
        pdf.set_font("Arial", style='B', size=12)
        pdf.cell(190, 10, txt=f"{mes_nome} {d['ano']}", ln=True, align='C')
        pdf.ln(5)

        # Tabela de saldos
        pdf.set_text_color(255, 255, 255)  # Cor branca
        pdf.set_font("Arial", style='B', size=10)
        pdf.set_fill_color(28, 30, 62)  # Azul Escuro
        pdf.cell(55, 10, "Saldo Inicial", border=1, align='C', fill=True)
        pdf.cell(40, 10, "Entradas", border=1, align='C', fill=True)
        pdf.cell(40, 10, "Saídas", border=1, align='C', fill=True)
        pdf.cell(55, 10, "Saldo Final", border=1, align='C', fill=True)
        pdf.ln()

        pdf.set_text_color(28, 30, 62)  # Azul Escuro
        pdf.set_font("Arial", size=11)
        pdf.cell(55, 10, f" {(d['saldo_inicial'])}", border=1, align='C')
        pdf.cell(40, 10, f" {(d['entradas'])}", border=1, align='C')
        pdf.cell(40, 10, f" {(d['saidas'])}", border=1, align='C')
        pdf.cell(55, 10, f" {(d['saldo_final'])}", border=1, align='C')
        pdf.ln(15)

        # Tabela de lançamentos
        pdf.set_font("Arial", style='B', size=10)
        pdf.set_fill_color(201, 203, 231)  # Cinza claro
        pdf.cell(35, 10, "Data", border=1, align='C', fill=True)
        pdf.cell(35, 10, "Tipo", border=1, align='C', fill=True)
        pdf.cell(65, 10, "Descrição", border=1, align='C', fill=True)
        pdf.cell(35, 10, "Valor", border=1, align='C', fill=True)
        pdf.cell(20, 10, "Cód.", border=1, align='C', fill=True)
        pdf.ln()
        
        pdf.set_text_color(28, 30, 62)
        pdf.set_font("Arial", size=10)
        for lanc in d['lancamentos']:
            pdf.cell(35, 10, txt=lanc.data.strftime('%d/%m/%Y'), border=1, align='C')
            pdf.cell(35, 10, txt=lanc.tipo, border=1, align='C')
            pdf.cell(65, 10, txt=lanc.descricao, border=1, align='C')
            pdf.cell(35, 10, txt=f" {formatar_moeda(lanc.valor)}", border=1, align='C')
            pdf.cell(20, 10, txt=str(lanc.id_lancamento), border=1, align='C')
            pdf.ln()

        pdf.ln(5)  # Espaço entre meses

    # Geração e envio do arquivo PDF
    pdf_file = f"relatorio_{ano}_id_usuario_{current_user.id}.pdf"
    relatorios_dir = app.config['RELATORIOS_FOLDER']  # Caminho para a pasta 'relatorios' no diretório raiz
    os.makedirs(relatorios_dir, exist_ok=True)  # Cria a pasta 'relatorios' se não existir
    pdf_path = os.path.join(relatorios_dir, pdf_file)  # Caminho completo do arquivo
    pdf.output(pdf_path)  # Salva o PDF na pasta 'relatorios'
    return send_file(pdf_path, as_attachment=True)  # Envia o arquivo ao usuário


@app.route('/validar/<string:hash_assinatura>')
def validar_assinatura(hash_assinatura):
    assinatura = AssinaturaRelatorio.query.filter_by(hash=hash_assinatura).first()

    if not assinatura:
        return render_template('validacao.html', valida=False)

    # Busca dados adicionais do usuário (opcional)
    usuario = Usuario.query.get(assinatura.id_usuario)
    config = Configuracao.query.filter_by(id_usuario=assinatura.id_usuario).first()

    return render_template(
        'validacao.html',
        valida=True,
        assinatura=assinatura,
        usuario=usuario,
        config=config
    )


def buscar_lancamentos(ano=None, mes=None):
    """Retorna os lançamentos filtrados por ano, mês e usuário logado."""
    query = Lancamento.query  # Começa a consulta no banco

    # Filtra sempre pelo usuário logado
    query = query.filter(Lancamento.id_usuario == current_user.id)

    if ano:
        query = query.filter(extract('year', Lancamento.data) == ano)  # Filtra pelo ano

    if mes:
        query = query.filter(extract('month', Lancamento.data) == mes)  # Filtra pelo mês

    return query.order_by(Lancamento.data.asc(), Lancamento.id.asc()).all()  # Retorna os lançamentos filtrados


@app.route('/exportar-comprovantes')
@login_required  # Garante que apenas usuários logados acessem essa rota
def exportar_comprovantes():
    mes = request.args.get('mes', type=int, default=None)
    dados = dados_relatorio(mes)  # Já ajustado para o usuário logado
    ano = dados[0]['ano_vigente'] if dados else datetime.now().year  # Obtém o ano vigente da configuração

    dados_config = dados[0].get('configuracao', {}) if dados else {}

    # Buscar lançamentos do período (já ajustado para o usuário logado)
    lancamentos = buscar_lancamentos(ano, mes)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    # Adicionar título
    pdf.set_text_color(28, 30, 62)  # Azul Escuro
    pdf.set_font("Arial", style='B', size=14)
    pdf.cell(190, 10, f"RELATÓRIO DE COMPROVANTES - {ano if ano else 'Todos'} {f'Mês {mes}' if mes else ''}", ln=True, align='C')
    pdf.ln(0)

    # Título com Nome da UMP/Federação centralizado
    pdf.set_font("Arial", style='B', size=12)
    campo = f"{dados_config.ump_federacao if hasattr(dados_config, 'ump_federacao') else 'Não definido'} - {dados_config.federacao_sinodo if hasattr(dados_config, 'federacao_sinodo') else 'Não definido'}"
    pdf.cell(190, 10, campo, ln=True, align='C')
    pdf.ln(10)  # Espaço após a linha

    # === Relação de Comprovantes ===
    pdf.set_text_color(255, 255, 255)  # Cor branca
    pdf.set_font("Arial", style='B', size=14)
    pdf.set_fill_color(28, 30, 62)  # Azul
    pdf.cell(190, 8, txt="Relação de Comprovantes", ln=True, align='C', fill=True)
    pdf.ln(5)

    # Criar a tabela de lançamentos
    pdf.set_text_color(28, 30, 62)  # Azul Escuro
    pdf.set_font("Arial", style='B', size=10)
    pdf.cell(15, 10, "Cód.", border=1, align='C')
    pdf.cell(30, 10, "Data", border=1, align='C')
    pdf.cell(80, 10, "Descrição", border=1, align='C')
    pdf.cell(30, 10, "Valor", border=1, align='C')
    pdf.cell(35, 10, "Comprovante", border=1, align='C')
    pdf.ln()

    pdf.set_font("Arial", size=10)

    for lanc in lancamentos:
        pdf.cell(15, 10, str(lanc.id_lancamento), border=1, align='C')
        pdf.cell(30, 10, txt=lanc.data.strftime('%d/%m/%Y'), border=1, align='C')
        pdf.cell(80, 10, lanc.descricao, border=1, align='L')
        pdf.cell(30, 10, f"R$ {lanc.valor:.2f}", border=1, align='C')
        pdf.cell(35, 10, "Anexado" if lanc.comprovante else "Não anexado", border=1, align='C')
        pdf.ln()

    pdf.ln(10)

    # Adicionar os comprovantes ao PDF
    for lanc in lancamentos:
        if lanc.comprovante:
            # Construir o caminho completo do comprovante
            comprovante_path = os.path.join(app.config['UPLOAD_FOLDER'], lanc.comprovante) if not os.path.isabs(lanc.comprovante) else lanc.comprovante

            print(f"Comprovante Path: {comprovante_path}")  # Para debug

            # Verifique se o arquivo existe e é uma imagem
            if os.path.exists(comprovante_path):
                file_extension = comprovante_path.lower().split('.')[-1]
                if file_extension in ['jpg', 'jpeg', 'png']:
                    try:
                        pdf.add_page()
                        pdf.set_font("Arial", style='B', size=12)
                        pdf.cell(190, 10, f"Comprovante - Cód. {lanc.id_lancamento}", ln=True, align='C')
                        pdf.ln(5)

                        # Carregar a imagem para obter as dimensões
                        image = Image.open(comprovante_path)
                        img_width, img_height = image.size

                        # Definir a largura máxima disponível no PDF
                        max_width = 190
                        max_height = 250  # Ajuste conforme necessário para não sobrepor outras informações

                        # Calcular o novo tamanho proporcionalmente
                        ratio = min(max_width / img_width, max_height / img_height)
                        new_width = img_width * ratio
                        new_height = img_height * ratio

                        # Adicionar a imagem redimensionada ao PDF
                        pdf.image(comprovante_path, x=10, y=30, w=new_width, h=new_height)

                    except Exception as e:
                        pdf.cell(190, 10, f"Erro ao carregar imagem: {str(e)}", ln=True, align='C')
                else:
                    pdf.add_page()
                    pdf.set_font("Arial", style='B', size=12)
                    pdf.cell(190, 10, f"Comprovante - ID {lanc.id_lancamento} (Formato de arquivo não suportado)", ln=True, align='C')
                    pdf.ln(5)
                    pdf.cell(190, 10, f"Comprovante: {comprovante_path} - Não é imagem", ln=True, align='C')
            else:
                pdf.add_page()
                pdf.set_font("Arial", style='B', size=12)
                pdf.cell(190, 10, f"Comprovante - ID {lanc.id_lancamento} (Arquivo não encontrado)", ln=True, align='C')

    # Define o nome do arquivo
    pdf_file = f"comprovantes_{ano}_id_usuario_{current_user.id}.pdf"
    
    # Usa a pasta persistente configurada
    relatorios_dir = app.config['RELATORIOS_FOLDER']
    os.makedirs(relatorios_dir, exist_ok=True)
    
    # Caminho completo do PDF
    pdf_path = os.path.join(relatorios_dir, pdf_file)
    
    # Salva o PDF
    pdf.output(pdf_path)


    return send_file(pdf_path, as_attachment=True)



@app.route('/orientacoes')
def orientacoes():
    return render_template('orientacoes.html')

@app.route('/consultar')
def consultar():
    return render_template('consultar.html')

@app.route('/buscar_relatorio', methods=['GET', 'POST'])
@login_required
def buscar_relatorio():
    relatorios_dir = app.config['RELATORIOS_FOLDER']  # Caminho para a pasta relatorios
    relatorio_encontrado = None

    if request.method == 'POST':
        ano = request.form.get('ano', type=int)
        if not ano:
            flash('Por favor, selecione um ano.', 'danger')
            return redirect(url_for('buscar_relatorio'))

        # Nome do arquivo esperado: "relatorio_{ano}_id_usuario_{id}.pdf"
        relatorio_nome = f"relatorio_{ano}_id_usuario_{current_user.id}.pdf"
        relatorio_path = os.path.join(relatorios_dir, relatorio_nome)

        if os.path.exists(relatorio_path):
            relatorio_encontrado = relatorio_nome
        else:
            flash(f'Relatório para o ano {ano} não encontrado.', 'warning')

    # Lista de anos para o formulário (últimos 05 anos)
    from datetime import datetime
    ano_atual = datetime.now().year
    anos = list(range(ano_atual - 4, ano_atual + 1))

    return render_template('buscar_relatorio.html', anos=anos, relatorio_encontrado=relatorio_encontrado)

@app.route('/visualizar_relatorio/<filename>')
@login_required
def visualizar_relatorio(filename):
    relatorios_dir = app.config['RELATORIOS_FOLDER']
    relatorio_path = os.path.join(relatorios_dir, filename)

    # Verifica se o arquivo pertence ao usuário logado
    if f"id_usuario_{current_user.id}" not in filename:
        flash('Você não tem permissão para visualizar este relatório.', 'danger')
        return redirect(url_for('buscar_relatorio'))

    if os.path.exists(relatorio_path):
        return send_file(relatorio_path, mimetype='application/pdf')
    else:
        flash('Relatório não encontrado.', 'danger')
        return redirect(url_for('buscar_relatorio'))

@app.route('/buscar_comprovantes', methods=['GET', 'POST'])
@login_required
def buscar_comprovantes():
    relatorios_dir = app.config['RELATORIOS_FOLDER']  # Caminho para a pasta relatorios
    relatorio_encontrado = None

    if request.method == 'POST':
        ano = request.form.get('ano', type=int)
        if not ano:
            flash('Por favor, selecione um ano.', 'danger')
            return redirect(url_for('buscar_comprovantes'))

        # Nome do arquivo esperado: "relatorio_{ano}_id_usuario_{id}.pdf"
        relatorio_nome = f"comprovantes_{ano}_id_usuario_{current_user.id}.pdf"
        relatorio_path = os.path.join(relatorios_dir, relatorio_nome)

        if os.path.exists(relatorio_path):
            relatorio_encontrado = relatorio_nome
        else:
            flash(f'Comprovantes para o ano {ano} não encontrados.', 'warning')

    # Lista de anos para o formulário (exemplo: últimos 10 anos)
    from datetime import datetime
    ano_atual = datetime.now().year
    anos = list(range(ano_atual - 4, ano_atual + 1))

    return render_template('buscar_comprovantes.html', anos=anos, relatorio_encontrado=relatorio_encontrado)

@app.route('/visualizar_comprovantes/<filename>')
@login_required
def visualizar_comprovantes(filename):
    relatorios_dir = app.config['RELATORIOS_FOLDER']
    relatorio_path = os.path.join(relatorios_dir, filename)

    # Verifica se o arquivo pertence ao usuário logado
    if f"id_usuario_{current_user.id}" not in filename:
        flash('Você não tem permissão para visualizar estes comprovantes.', 'danger')
        return redirect(url_for('buscar_comprovantes'))

    if os.path.exists(relatorio_path):
        return send_file(relatorio_path, mimetype='application/pdf')
    else:
        flash('Comprovantes não encontrados.', 'danger')
        return redirect(url_for('buscar_comprovantes'))




def carregar_administradores():
    """Carrega a relação entre administradores e usuários como um grafo."""
    administradores = defaultdict(list)
    registros = Configuracao.query.all()

    # Construindo o grafo
    for registro in registros:
        if registro.admin is not None:  # Evita que um administrador sem superior seja adicionado ao grafo
            administradores[registro.admin].append(registro.id_usuario)

    return administradores

def get_usuarios_autorizados():
    """Retorna todos os usuários que o administrador atual pode acessar, incluindo hierarquia."""
    administradores = carregar_administradores()
    autorizados = set()  # Conjunto para armazenar os usuários autorizados
    visitados = set()  # Conjunto para evitar recursão infinita

    # Fila para armazenar os usuários a serem processados
    fila = [current_user.id]  

    # Processa os usuários da fila
    while fila:
        usuario_id = fila.pop(0)

        if usuario_id not in visitados:
            visitados.add(usuario_id)  # Marca o usuário como visitado
            if usuario_id in administradores:
                for usuario in administradores[usuario_id]:
                    if usuario not in visitados:  # Verifica se o usuário já foi visitado
                        fila.append(usuario)  # Adiciona os subordinados à fila
                        autorizados.add(usuario)  # Marca como autorizado

    # Buscando todos os dados de uma vez para evitar consultas repetidas
    usuarios_autorizados = Configuracao.query.filter(Configuracao.id_usuario.in_(autorizados)).all()

    # Organizando os dados para exibição
    return [
        {"id_usuario": usuario.id_usuario, 
         "ump_federacao": usuario.ump_federacao or "Nome não disponível"}
        for usuario in usuarios_autorizados
    ]


@app.route('/admin_consultar')
@login_required
def admin_consultar():
    # Buscar a configuração do usuário logado
    config = Configuracao.query.filter_by(id_usuario=current_user.id).first()

    # Verifica se o usuário tem permissão (se é gestor)
    if not config or config.gestor != "Sim":
        flash("Você não tem permissão para acessar esta página.", "danger")
        return redirect(url_for("index"))

    administradores = carregar_administradores()
    usuarios_autorizados = administradores.get(current_user.id, [])

    return render_template('admin_consultar.html', usuarios_autorizados=usuarios_autorizados)

@app.route('/admin/buscar_relatorio', methods=['GET', 'POST'])
@login_required
def admin_buscar_relatorio():
    administradores = carregar_administradores()

    # Função para verificar se um usuário tem acesso a outro com base na hierarquia
    def tem_permissao(usuario_id, id_usuario_verificado):
        """Verifica se um usuário tem permissão para acessar outro."""
        if usuario_id == id_usuario_verificado:
            return True  # Acesso direto

        # Usando um conjunto para evitar visitar os mesmos usuários
        visitados = set()
        fila = [usuario_id]

        while fila:
            usuario_atual = fila.pop(0)
            if usuario_atual == id_usuario_verificado:
                return True  # Encontrei um caminho de permissão

            if usuario_atual not in visitados:
                visitados.add(usuario_atual)
                fila.extend(administradores.get(usuario_atual, []))  # Adiciona os subordinados à fila

        return False  # Não encontrou caminho de permissão

    # Verifica se o usuário logado tem permissão para acessar
    if current_user.id not in administradores:
        flash("Você não tem nenhum usuário cadastrado para consultar.", "danger")
        return redirect(url_for("admin_consultar"))

    # Diretório onde os relatórios estão armazenados
    relatorios_dir = app.config['RELATORIOS_FOLDER']
    relatorio_encontrado = None
    usuario_selecionado = None  

    # Função para expandir a lista de usuários autorizados, incluindo subordinados
    def expandir_autorizados(usuario_id):
        autorizados = set()
        fila = [usuario_id]
        while fila:
            usuario_atual = fila.pop(0)
            if usuario_atual not in autorizados:
                autorizados.add(usuario_atual)
                fila.extend(administradores.get(usuario_atual, []))  # Expande a recursão para os subordinados
        return autorizados

    # Obtém os IDs dos usuários autorizados, incluindo todos os subordinados
    usuarios_autorizados_ids = expandir_autorizados(current_user.id)

    # Busca os usuários autorizados no banco de dados
    usuarios_autorizados = db.session.query(Configuracao.id_usuario, Configuracao.ump_federacao) \
    .filter(Configuracao.id_usuario.in_(usuarios_autorizados_ids)) \
    .all()

    # Formata os dados para exibição
    usuarios_autorizados = [
        {"id_usuario": usuario.id_usuario, 
        "ump_federacao": usuario.ump_federacao or "Nome não disponível"}
        for usuario in usuarios_autorizados
        if usuario.id_usuario != current_user.id
    ]

    if request.method == 'POST':
        ano = request.form.get('ano', type=int)
        usuario_id = request.form.get('usuario_id', type=int)

        if not ano or not usuario_id:
            flash('Por favor, selecione um ano e um usuário.', 'danger')
            return redirect(url_for('admin_buscar_relatorio'))

        # Verifica se o usuário logado tem permissão para acessar o usuário selecionado
        if not tem_permissao(current_user.id, usuario_id):
            flash(f'Você não tem permissão para acessar relatórios deste usuário (ID: {usuario_id}).', 'danger')
            return redirect(url_for('admin_buscar_relatorio'))

        relatorio_nome = f"relatorio_{ano}_id_usuario_{usuario_id}.pdf"
        relatorio_path = os.path.join(relatorios_dir, relatorio_nome)

        if os.path.isfile(relatorio_path):  # Usa isfile() para garantir que é um arquivo
            relatorio_encontrado = relatorio_nome
        else:
            flash(f'Relatório para o ano {ano} não encontrado para este usuário.', 'warning')

        usuario_selecionado = usuario_id  

    # Lista os últimos 5 anos
    ano_atual = datetime.now().year
    anos = list(range(ano_atual - 4, ano_atual + 1))

    return render_template('admin_buscar_relatorio.html', 
                           anos=anos, 
                           relatorio_encontrado=relatorio_encontrado,
                           usuarios_autorizados=usuarios_autorizados,
                           usuario_selecionado=usuario_selecionado)

@app.route('/admin/visualizar_relatorio/<filename>')
@login_required
def admin_visualizar_relatorio(filename):
    """O administrador visualiza o relatório de um usuário permitido."""
    usuarios_autorizados = get_usuarios_autorizados()
    if not usuarios_autorizados:
        flash("Acesso negado.", "danger")
        return redirect(url_for('consultar'))

    relatorios_dir = app.config['RELATORIOS_FOLDER']
    relatorio_path = os.path.join(relatorios_dir, filename)

    # Pega o ID do usuário dentro do nome do arquivo
    try:
        usuario_id = int(filename.split("_id_usuario_")[1].split(".")[0])
    except (IndexError, ValueError):
        flash("Nome de arquivo inválido.", "danger")
        return redirect(url_for('admin_consultar'))

    if usuario_id not in [usuario["id_usuario"] for usuario in usuarios_autorizados]:
        flash("Você não tem permissão para visualizar este relatório.", "danger")
        return redirect(url_for('admin_consultar'))

    if os.path.exists(relatorio_path):
        return send_file(relatorio_path, mimetype='application/pdf')
    else:
        flash('Relatório não encontrado.', 'danger')
        return redirect(url_for('admin_consultar'))

@app.route('/admin/buscar_comprovantes', methods=['GET', 'POST'])
@login_required
def admin_buscar_comprovantes():
    administradores = carregar_administradores()

    # Função para verificar se um usuário tem acesso a outro com base na hierarquia
    def tem_permissao(usuario_id, id_usuario_verificado):
        """Verifica se um usuário tem permissão para acessar outro."""
        if usuario_id == id_usuario_verificado:
            return True  # Acesso direto

        # Usando um conjunto para evitar visitar os mesmos usuários
        visitados = set()
        fila = [usuario_id]

        while fila:
            usuario_atual = fila.pop(0)
            if usuario_atual == id_usuario_verificado:
                return True  # Encontrei um caminho de permissão

            if usuario_atual not in visitados:
                visitados.add(usuario_atual)
                fila.extend(administradores.get(usuario_atual, []))  # Adiciona os subordinados à fila

        return False  # Não encontrou caminho de permissão

    # Verifica se o usuário logado tem permissão para acessar
    if current_user.id not in administradores:
        flash("Você não tem nenhum usuário cadastrado para consultar.", "danger")
        return redirect(url_for("admin_consultar"))

    # Diretório onde os relatórios estão armazenados
    relatorios_dir = app.config['RELATORIOS_FOLDER']
    relatorio_encontrado = None
    usuario_selecionado = None  

    # Função para expandir a lista de usuários autorizados, incluindo subordinados
    def expandir_autorizados(usuario_id):
        autorizados = set()
        fila = [usuario_id]
        while fila:
            usuario_atual = fila.pop(0)
            if usuario_atual not in autorizados:
                autorizados.add(usuario_atual)
                fila.extend(administradores.get(usuario_atual, []))  # Expande a recursão para os subordinados
        return autorizados

    # Obtém os IDs dos usuários autorizados, incluindo todos os subordinados
    usuarios_autorizados_ids = expandir_autorizados(current_user.id)

    # Busca os usuários autorizados no banco de dados
    usuarios_autorizados = db.session.query(Configuracao.id_usuario, Configuracao.ump_federacao) \
    .filter(Configuracao.id_usuario.in_(usuarios_autorizados_ids)) \
    .all()

    # Formata os dados para exibição
    usuarios_autorizados = [
        {"id_usuario": usuario.id_usuario, 
        "ump_federacao": usuario.ump_federacao or "Nome não disponível"}
        for usuario in usuarios_autorizados
        if usuario.id_usuario != current_user.id
    ]

    if request.method == 'POST':
        ano = request.form.get('ano', type=int)
        usuario_id = request.form.get('usuario_id', type=int)

        if not ano or not usuario_id:
            flash('Por favor, selecione um ano e um usuário.', 'danger')
            return redirect(url_for('admin_buscar_comprovantes'))

        # Verifica se o usuário logado tem permissão para acessar o usuário selecionado
        if not tem_permissao(current_user.id, usuario_id):
            flash(f'Você não tem permissão para acessar comprovantes deste usuário (ID: {usuario_id}).', 'danger')
            return redirect(url_for('admin_buscar_comprovantes'))

        relatorio_nome = f"comprovantes_{ano}_id_usuario_{usuario_id}.pdf"
        relatorio_path = os.path.join(relatorios_dir, relatorio_nome)

        if os.path.isfile(relatorio_path):  # Usa isfile() para garantir que é um arquivo
            relatorio_encontrado = relatorio_nome
        else:
            flash(f'Comprovantes para o ano {ano} não encontrados para este usuário.', 'warning')

        usuario_selecionado = usuario_id  

    # Lista os últimos 5 anos
    ano_atual = datetime.now().year
    anos = list(range(ano_atual - 4, ano_atual + 1))

    return render_template('admin_buscar_comprovantes.html', 
                           anos=anos, 
                           relatorio_encontrado=relatorio_encontrado,
                           usuarios_autorizados=usuarios_autorizados,
                           usuario_selecionado=usuario_selecionado)

@app.route('/admin/visualizar_comprovantes/<filename>')
@login_required
def admin_visualizar_comprovantes(filename):
    """O administrador visualiza os comprovantes de um usuário permitido."""
    usuarios_autorizados = get_usuarios_autorizados()
    if not usuarios_autorizados:
        flash("Acesso negado.", "danger")
        return redirect(url_for('consultar'))

    relatorios_dir = app.config['RELATORIOS_FOLDER']
    relatorio_path = os.path.join(relatorios_dir, filename)

    # Pega o ID do usuário dentro do nome do arquivo
    try:
        usuario_id = int(filename.split("_id_usuario_")[1].split(".")[0])
    except (IndexError, ValueError):
        flash("Nome de arquivo inválido.", "danger")
        return redirect(url_for('admin_consultar'))

    if usuario_id not in [usuario["id_usuario"] for usuario in usuarios_autorizados]:
        flash("Você não tem permissão para visualizar este relatório.", "danger")
        return redirect(url_for('admin_consultar'))

    if os.path.exists(relatorio_path):
        return send_file(relatorio_path, mimetype='application/pdf')
    else:
        flash('Comprovantes não encontrados.', 'danger')
        return redirect(url_for('admin_consultar'))



@app.route('/excluir_todos_lancamentos', methods=['GET', 'POST'])
@login_required
def excluir_todos_lancamentos():
    if request.method == 'POST':
        with app.app_context():
            # Exclui os lançamentos e comprovantes
            lancamentos = Lancamento.query.filter_by(id_usuario=current_user.id).all()

            if not lancamentos:
                flash('Nenhum lançamento encontrado para exclusão.', 'warning')
                return redirect(url_for('excluir_todos_lancamentos'))

            for lancamento in lancamentos:
                if lancamento.comprovante:
                    comprovante_path = lancamento.comprovante
                    if os.path.exists(comprovante_path):
                        os.remove(comprovante_path)
                        print(f"Comprovante excluído: {comprovante_path}")
                    else:
                        print(f"Arquivo não encontrado: {comprovante_path}")

                db.session.delete(lancamento)

            db.session.commit()

            # Recarrega configurações
            configuracao = Configuracao.query.filter_by(id_usuario=current_user.id).first()
            if not configuracao:
                flash("Erro: Configuração não encontrada.", "danger")
                return redirect(url_for('index'))

            ano_base = configuracao.ano_vigente
            sinodal = configuracao.sinodal == "Sim"
            mes_inicio = configuracao.mes_inicio_bienio if sinodal else 1
            total_meses = 27 if sinodal else 12

            # Remove todos os saldos antigos
            SaldoFinal.query.filter_by(id_usuario=current_user.id).delete()
            db.session.commit()

            # Recria saldo inicial no mês/ano de início da gestão
            saldo_inicial = configuracao.saldo_inicial or 0
            salvar_saldo_final(mes_inicio, ano_base, saldo_inicial)

            # Propaga saldos a partir do mês/ano inicial da gestão
            propagar_recalculo_a_partir(mes_inicial=mes_inicio, ano_base=ano_base)

            flash('Todos os lançamentos e comprovantes foram excluídos com sucesso!', 'success')

        return redirect(url_for('excluir_todos_lancamentos'))

    return render_template('excluir_lancamentos.html')



load_dotenv()

api_key = os.getenv("SENDINBLUE_API_KEY")

# Rota para recuperação de senha
@app.route('/recuperar_senha', methods=['GET', 'POST'])
def recuperar_senha():
    if request.method == 'POST':
        email = request.form['email']

        # Validar formato do email
        if not validar_email(email):
            return render_template('erro.html', mensagem="Formato de e-mail inválido.")

        # Verificar se o email existe na tabela configuracao
        user_id = verificar_email_no_banco(email)

        if user_id:
            # Gerar uma senha aleatória numérica de 6 dígitos
            nova_senha = gerar_senha_aleatoria()

            # Atualizar a senha no banco de dados
            atualizar_senha_no_banco(user_id, nova_senha)

            # Enviar a senha para o email do usuário
            sucesso_email = enviar_email_sendinblue(email, nova_senha)
            if sucesso_email:
                return render_template('mensagem_sucesso.html', email=email)
            else:
                return render_template('erro.html', mensagem="Houve um problema ao enviar o e-mail. Tente novamente mais tarde.")
        else:
            return render_template('erro.html', mensagem="E-mail não encontrado.")

    return render_template('recuperar_senha.html')

# Função para verificar se o email existe na tabela 'configuracao'
def verificar_email_no_banco(email):
    user = Configuracao.query.filter_by(email=email).first()
    if user:
        return user.id_usuario
    return None

# Função para gerar uma senha aleatória numérica de 6 dígitos
def gerar_senha_aleatoria():
    return str(random.randint(100000, 999999))  # Gera uma senha numérica de 6 dígitos

# Função para atualizar a senha no banco de dados
def atualizar_senha_no_banco(user_id, nova_senha):
    usuario = Usuario.query.filter_by(id=user_id).first()
    if usuario:
        usuario.senha = nova_senha
        db.session.commit()

# Função para validar o formato do e-mail
def validar_email(email):
    email_regex = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
    return re.match(email_regex, email) is not None

# Função para enviar o e-mail via Sendinblue
def enviar_email_sendinblue(email_destinatario, nova_senha):
    api_key = os.getenv("SENDINBLUE_API_KEY")

    url = "https://api.sendinblue.com/v3/smtp/email"

    headers = {
        "api-key": api_key,
        "Content-Type": "application/json"
    }

    data = {
        "sender": {"email": "suporteumpfinanceiro@gmail.com"},
        "to": [{"email": email_destinatario}],
        "subject": "Recuperação de Senha",
        "textContent": f"Olá!\n\nSua nova senha é: {nova_senha}\n\nEste é um e-mail automático. Por favor, não responda a esta mensagem."
    }

    resposta = requests.post(url, json=data, headers=headers)

    # Verifica se o código de resposta é 200 ou 201
    if resposta.status_code in [200, 201]:
        print("E-mail enviado com sucesso!")
        return True
    else:
        print(f"Erro ao enviar o e-mail: {resposta.status_code} - {resposta.text}")
        return False




@app.route('/cadastro', methods=['GET', 'POST'])
@login_required
def cadastro():
    if request.method == 'POST':
        # Recebe os dados do formulário
        username = request.form['username']
        senha = request.form['senha']
        gestor = request.form.get('gestor', 'Não')  # Obtém o valor do campo gestor (padrão: "Não")

        # Cria o novo usuário com is_active = 1
        novo_usuario = Usuario(username=username, senha=senha, is_active=1)
        db.session.add(novo_usuario)
        db.session.commit()

        # Recupera o ID do novo usuário e o ID do usuário logado
        id_usuario = novo_usuario.id
        id_admin = current_user.id

        # Insere a configuração do novo usuário na tabela 'configuracao'
        email = f"{username}@ump.com"
        configuracao = Configuracao(
            id_usuario=id_usuario,
            admin=id_admin,
            gestor=gestor,
            sinodal='Não',  # Adiciona o valor de "Sim" ou "Não"
            ump_federacao='Vazio',
            federacao_sinodo='Vazio',
            ano_vigente=datetime.now().year,
            socios_ativos=0,
            socios_cooperadores=0,
            tesoureiro_responsavel='Vazio',
            presidente_responsavel='Vazio',
            saldo_inicial=0.0,
            email=email,
            ultimo_id_lancamento=0,
            mes_inicio_bienio=1
        )
        db.session.add(configuracao)
        db.session.commit()

        # Insere as linhas de saldo_final (para os 12 meses)
        for mes in range(1, 13):
            saldo_final = SaldoFinal(id_usuario=id_usuario, mes=mes, ano=datetime.now().year, saldo=0.0)
            db.session.add(saldo_final)
        db.session.commit()
  

        # Flash message de sucesso
        flash('Usuário cadastrado com sucesso!', 'success')

        atualizar_socios_usuario()

        return redirect(url_for('cadastro'))  # Redireciona para a mesma página

    return render_template('cadastro.html')

@app.route('/usuarios_cadastrados')
@login_required
def usuarios_cadastrados():
    # Recupera todas as configurações vinculadas ao admin logado
    configuracoes = Configuracao.query.filter_by(admin=current_user.id).all()

    # Junta os dados com os usuários correspondentes, exceto o usuário logado
    usuarios = []
    for config in configuracoes:
        usuario = Usuario.query.get(config.id_usuario)
        if usuario and usuario.id != current_user.id:
            usuarios.append({
                'id': usuario.id,
                'username': usuario.username,
                'is_active': usuario.is_active,
                'ump_federacao': config.ump_federacao,
                'gestor': config.gestor
            })

    return render_template('usuarios_cadastrados.html', usuarios=usuarios)

@app.route('/usuario/<int:id>/desativar')
@login_required
def desativar_usuario(id):
    usuario = Usuario.query.get_or_404(id)
    config = Configuracao.query.filter_by(id_usuario=id, admin=current_user.id).first()
    if config:
        usuario.is_active = 0
        db.session.commit()
        flash('Usuário desativado com sucesso.', 'success')

        atualizar_socios_usuario()

    return redirect(url_for('usuarios_cadastrados'))

@app.route('/usuario/<int:id>/ativar')
@login_required
def ativar_usuario(id):
    usuario = Usuario.query.get_or_404(id)
    config = Configuracao.query.filter_by(id_usuario=id, admin=current_user.id).first()
    if config:
        usuario.is_active = 1
        db.session.commit()
        flash('Usuário ativado com sucesso.', 'success')

        atualizar_socios_usuario()

    return redirect(url_for('usuarios_cadastrados'))


@app.route('/resetar_senha/<int:id>')
@login_required
def resetar_senha(id):
    usuario = Usuario.query.get_or_404(id)
    config = Configuracao.query.filter_by(id_usuario=id, admin=current_user.id).first()

    if config:
        usuario.senha = 123456
        db.session.commit()
        flash(f'Senha do usuário "{usuario.username}" foi redefinida com sucesso para "123456".', 'success')
    else:
        flash('Usuário não encontrado ou você não tem permissão para editar.', 'danger')

    return redirect(url_for('usuarios_cadastrados'))


@app.route('/editar_usuario/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_usuario(id):
    # Buscar usuário e configuração vinculados ao admin logado
    config = Configuracao.query.filter_by(id_usuario=id, admin=current_user.id).first()
    usuario = Usuario.query.get(id)

    if not config or not usuario:
        flash("Usuário não encontrado ou não autorizado.", "danger")
        return redirect(url_for('usuarios_cadastrados'))

    if request.method == 'POST':
        novo_username = request.form['username']
        novo_gestor = request.form['gestor']
        novo_ump_federacao = request.form['ump_federacao']

        usuario.username = novo_username
        config.gestor = novo_gestor
        config.ump_federacao = novo_ump_federacao

        db.session.commit()
        flash("Usuário atualizado com sucesso.", "success")
        return redirect(url_for('usuarios_cadastrados'))

    return render_template('editar_usuario.html', usuario=usuario, config=config)


MES_NOMES = {
    1: 'Jan', 2: 'Fev', 3: 'Mar', 4: 'Abr', 5: 'Mai', 6: 'Jun',
    7: 'Jul', 8: 'Ago', 9: 'Set', 10: 'Out', 11: 'Nov', 12: 'Dez'
}


# Socio routes
@app.route('/cadastrar_socio', methods=['GET', 'POST'])
@login_required
def cadastrar_socio():
    if request.method == 'POST':
        nome = request.form['nome'].strip()
        tipo = request.form['tipo']

        if not nome:
            flash('O nome é obrigatório!', 'error')
            return render_template('cadastrar_socio.html')
        if tipo not in ['Ativo', 'Cooperador']:
            flash('Tipo de sócio inválido!', 'error')
            return render_template('cadastrar_socio.html')

        try:
            novo_socio = Socio(
                id_usuario=current_user.id,
                nome=nome,
                tipo=tipo
            )
            db.session.add(novo_socio)
            db.session.commit()
            flash('Sócio cadastrado com sucesso!', 'success')
            atualizar_socios_usuario()
            return redirect(url_for('listar_socios'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar sócio: {str(e)}', 'error')

    return render_template('cadastrar_socio.html')

@app.route('/socios', methods=['GET'])
@login_required
def listar_socios():
    # Fetch socios
    socios = Socio.query.filter_by(id_usuario=current_user.id).order_by(Socio.nome.asc()).all()

    return render_template(
        'socios.html',
        socios=socios
    )


@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    current_year = date.today().year
    current_month = date.today().month
    now = datetime.now()

    # Handle month/year selection for mensalidade total
    selected_month = request.form.get('mes', current_month, type=int) if request.method == 'POST' else current_month
    selected_year = request.form.get('ano', current_year, type=int) if request.method == 'POST' else current_year

    # Validate month/year
    if selected_month not in range(1, 13):
        selected_month = current_month
        flash('Mês inválido, usando mês atual.', 'error')
    if selected_year < 2000 or selected_year > current_year + 1:
        selected_year = current_year
        flash('Ano inválido, usando ano atual.', 'error')

    # Fetch socios
    socios = Socio.query.filter_by(id_usuario=current_user.id).order_by(Socio.nome.asc()).all()

    # Count Ativo and Cooperador socios
    count_ativo = sum(1 for s in socios if s.tipo == 'Ativo')
    count_cooperador = sum(1 for s in socios if s.tipo == 'Cooperador')

    # ACI metrics
    aci_valor_ano = AciValorAno.query.filter_by(id_usuario=current_user.id, ano=current_year).first()
    aci_configurado = float(aci_valor_ano.valor) if aci_valor_ano else 0.0
    aci_esperado = float(aci_configurado * count_ativo)
    aci_recebido = float(db.session.query(func.sum(AciPagamento.valor_pago)).filter_by(id_usuario=current_user.id, ano=current_year).scalar() or 0.0)
    aci_restante = max(0.0, aci_esperado - aci_recebido)

    # Mensalidade total for selected month/year
    mensalidade_total = float(db.session.query(func.sum(Mensalidade.valor_pago)).filter(
        Mensalidade.id_usuario == current_user.id,
        extract('month', Mensalidade.data_pagamento) == selected_month,
        extract('year', Mensalidade.data_pagamento) == selected_year
    ).scalar() or 0.0)

    # Pending and overdue socios
    socios_pendentes = []
    socios_atrasados = []
    for socio in socios:
        # Check for selected month/year
        mensalidade_selected = Mensalidade.query.filter_by(
            id_socio=socio.id,
            id_usuario=current_user.id,
            ano=selected_year,
            mes=selected_month
        ).first()
        if not mensalidade_selected:
            socios_pendentes.append(socio)

        # Check for previous months (up to selected_month - 1)
        for mes in range(1, selected_month):
            mensalidade = Mensalidade.query.filter_by(
                id_socio=socio.id,
                id_usuario=current_user.id,
                ano=selected_year,
                mes=mes
            ).first()
            if not mensalidade:
                socios_atrasados.append(socio)
                break  # Stop after finding one overdue month

    # ACI payment status
    socios_aci_pagos = []
    socios_aci_nao_pagos = []
    for socio in socios:
        if socio.tipo == 'Ativo':
            aci_pagamentos = AciPagamento.query.filter_by(
                id_socio=socio.id,
                id_usuario=current_user.id,
                ano=current_year
            ).all()
            total_pago = sum(p.valor_pago for p in aci_pagamentos)
            if total_pago >= aci_configurado:
                socios_aci_pagos.append(socio)
            else:
                socios_aci_nao_pagos.append(socio)

    # Format values
    dashboard_data = {
        'count_ativo': count_ativo,
        'count_cooperador': count_cooperador,
        'aci_configurado': formatar_moeda(aci_configurado),
        'aci_esperado': aci_esperado,
        'aci_recebido': aci_recebido,
        'aci_restante': aci_restante,
        'aci_configurado_fmt': formatar_moeda(aci_configurado),
        'aci_esperado_fmt': formatar_moeda(aci_esperado),
        'aci_recebido_fmt': formatar_moeda(aci_recebido),
        'aci_restante_fmt': formatar_moeda(aci_restante),
        'mensalidade_total': formatar_moeda(mensalidade_total),
        'selected_month': selected_month,
        'selected_month_name': MES_NOMES[selected_month],
        'selected_year': selected_year
    }

    return render_template(
        'dashboard.html',
        dashboard_data=dashboard_data,
        socios_pendentes=socios_pendentes,
        socios_atrasados=socios_atrasados,
        socios_aci_pagos=socios_aci_pagos,
        socios_aci_nao_pagos=socios_aci_nao_pagos,
        current_year=current_year,
        current_month=current_month,
        meses=[(i, MES_NOMES[i]) for i in range(1, 13)],
        MES_NOMES=MES_NOMES,
        now=now
    )


@app.route('/editar_socio/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_socio(id):
    socio = Socio.query.filter_by(id=id, id_usuario=current_user.id).first_or_404()
    if request.method == 'POST':
        nome = request.form['nome'].strip()
        tipo = request.form['tipo']

        if not nome:
            flash('O nome é obrigatório!', 'error')
            return render_template('editar_socio.html', socio=socio)
        if tipo not in ['Ativo', 'Cooperador']:
            flash('Tipo de sócio inválido!', 'error')
            return render_template('editar_socio.html', socio=socio)

        try:
            socio.nome = nome
            socio.tipo = tipo
            db.session.commit()
            flash('Sócio atualizado com sucesso!', 'success')
            atualizar_socios_usuario()
            return redirect(url_for('listar_socios'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar sócio: {str(e)}', 'error')


    return render_template('editar_socio.html', socio=socio)

@app.route('/excluir_socio/<int:id>', methods=['POST'])
@login_required
def excluir_socio(id):
    socio = Socio.query.filter_by(id=id, id_usuario=current_user.id).first_or_404()
    try:
        db.session.delete(socio)
        db.session.commit()
        flash('Sócio excluído com sucesso!', 'success')
        atualizar_socios_usuario()
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir sócio: {str(e)}', 'error')

    return redirect(url_for('listar_socios'))

# Mensalidade routes
@app.route('/detalhes_socio/<int:id_socio>')
@login_required
def detalhes_socio(id_socio):
    socio = Socio.query.filter_by(id=id_socio, id_usuario=current_user.id).first_or_404()
    mensalidades = Mensalidade.query.filter_by(id_socio=id_socio).order_by(Mensalidade.ano.desc(), Mensalidade.mes.desc()).all()

    # Format mensalidades with month names and currency
    mensalidades_formatted = [
        {
            'id': m.id,
            'ano': m.ano,
            'mes': MES_NOMES[m.mes],
            'valor_pago': formatar_moeda(m.valor_pago),
            'data_pagamento': m.data_pagamento.strftime('%d/%m/%Y')
        } for m in mensalidades
    ]

    # Calculate paid and pending months for the current year
    current_year = date.today().year
    mensalidades_current_year = [m for m in mensalidades if m.ano == current_year]
    meses_pagos = [MES_NOMES[m.mes] for m in mensalidades_current_year]
    meses_pendentes = [MES_NOMES[i] for i in range(1, 13) if MES_NOMES[i] not in meses_pagos]

    # ACI data for Ativo socios
    aci_data = None
    if socio.tipo == 'Ativo':
        aci_valor_ano = AciValorAno.query.filter_by(id_usuario=current_user.id, ano=current_year).first()
        aci_pagamentos = AciPagamento.query.filter_by(id_socio=id_socio, ano=current_year).all()
        valor_esperado = aci_valor_ano.valor if aci_valor_ano else 0.0
        valor_pago = sum(p.valor_pago for p in aci_pagamentos)
        valor_restante = max(0.0, valor_esperado - valor_pago)

        aci_data = {
            'valor_esperado': formatar_moeda(valor_esperado),
            'valor_pago': formatar_moeda(valor_pago),
            'valor_restante': formatar_moeda(valor_restante),
            'pagamentos': [
                {
                    'id': p.id,
                    'valor_pago': formatar_moeda(p.valor_pago),
                    'data_pagamento': p.data_pagamento.strftime('%d/%m/%Y')
                } for p in aci_pagamentos
            ]
        }

    return render_template(
        'detalhes_socio.html',
        socio=socio,
        mensalidades=mensalidades_formatted,
        meses_pagos=meses_pagos,
        meses_pendentes=meses_pendentes,
        current_year=current_year,
        aci_data=aci_data
    )

@app.route('/cadastrar_mensalidade/<int:id_socio>', methods=['GET', 'POST'])
@login_required
def cadastrar_mensalidade(id_socio):
    socio = Socio.query.filter_by(id=id_socio, id_usuario=current_user.id).first_or_404()
    current_year = date.today().year

    # Get paid months for the selected year
    ano = request.form.get('ano', current_year, type=int) if request.method == 'POST' else current_year
    mensalidades = Mensalidade.query.filter_by(id_socio=id_socio, ano=ano).all()
    meses_pagos = {m.mes for m in mensalidades}
    meses_disponiveis = [(i, MES_NOMES[i]) for i in range(1, 13) if i not in meses_pagos]

    if request.method == 'POST':
        try:
            ano = int(request.form['ano'])
            mes = int(request.form['mes'])
            valor_pago = float(request.form['valor_pago'])
            data_pagamento = datetime.strptime(request.form['data_pagamento'], '%Y-%m-%d').date()

            if mes not in range(1, 13):
                flash('Mês inválido!', 'error')
                return render_template('cadastrar_mensalidade.html', socio=socio, current_year=current_year, meses_disponiveis=meses_disponiveis)
            if valor_pago < 0:
                flash('Valor pago não pode ser negativo!', 'error')
                return render_template('cadastrar_mensalidade.html', socio=socio, current_year=current_year, meses_disponiveis=meses_disponiveis)

            mensalidade = Mensalidade(
                id_socio=socio.id,
                id_usuario=current_user.id,
                ano=ano,
                mes=mes,
                valor_pago=valor_pago,
                data_pagamento=data_pagamento
            )
            db.session.add(mensalidade)
            db.session.commit()
            flash('Mensalidade registrada com sucesso!', 'success')
            return redirect(url_for('detalhes_socio', id_socio=socio.id))
        except ValueError:
            flash('Formato de data ou valor inválido!', 'error')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao registrar mensalidade: {str(e)}', 'error')

    return render_template('cadastrar_mensalidade.html', socio=socio, current_year=current_year, meses_disponiveis=meses_disponiveis)

@app.route('/excluir_mensalidade/<int:id>', methods=['POST'])
@login_required
def excluir_mensalidade(id):
    mensalidade = Mensalidade.query.filter_by(id=id, id_usuario=current_user.id).first_or_404()
    id_socio = mensalidade.id_socio
    try:
        db.session.delete(mensalidade)
        db.session.commit()
        flash('Mensalidade excluída com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir mensalidade: {str(e)}', 'error')
    return redirect(url_for('detalhes_socio', id_socio=id_socio))

# ACI routes
@app.route('/configurar_aci', methods=['GET', 'POST'])
@login_required
def configurar_aci():
    if request.method == 'POST':
        try:
            ano = int(request.form['ano'])
            valor = float(request.form['valor'])

            if valor < 0:
                flash('Valor da ACI não pode ser negativo!', 'error')
                return render_template('configurar_aci.html')

            aci_valor_ano = AciValorAno.query.filter_by(id_usuario=current_user.id, ano=ano).first()
            if aci_valor_ano:
                aci_valor_ano.valor = valor
            else:
                aci_valor_ano = AciValorAno(
                    id_usuario=current_user.id,
                    ano=ano,
                    valor=valor
                )
                db.session.add(aci_valor_ano)

            db.session.commit()
            flash('Valor da ACI configurado com sucesso!', 'success')
            return redirect(url_for('listar_socios'))
        except ValueError:
            flash('Formato de ano ou valor inválido!', 'error')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao configurar ACI: {str(e)}', 'error')

    return render_template('configurar_aci.html', current_year=date.today().year)

@app.route('/cadastrar_aci_pagamento/<int:id_socio>', methods=['GET', 'POST'])
@login_required
def cadastrar_aci_pagamento(id_socio):
    socio = Socio.query.filter_by(id=id_socio, id_usuario=current_user.id).first_or_404()
    if socio.tipo != 'Ativo':
        flash('Apenas sócios Ativos pagam ACI!', 'error')
        return redirect(url_for('detalhes_socio', id_socio=id_socio))

    current_year = date.today().year
    aci_valor_ano = AciValorAno.query.filter_by(id_usuario=current_user.id, ano=current_year).first()
    if not aci_valor_ano:
        flash('Configure o valor da ACI para o ano atual primeiro!', 'error')
        return redirect(url_for('configurar_aci'))

    # Calculate remaining amount
    pagamentos = AciPagamento.query.filter_by(id_socio=id_socio, ano=current_year).all()
    valor_pago = sum(p.valor_pago for p in pagamentos)
    valor_restante = max(0.0, aci_valor_ano.valor - valor_pago)

    if request.method == 'POST':
        try:
            valor_pago_novo = float(request.form['valor_pago'])
            data_pagamento = datetime.strptime(request.form['data_pagamento'], '%Y-%m-%d').date()

            if valor_pago_novo < 0:
                flash('Valor pago não pode ser negativo!', 'error')
                return render_template('cadastrar_aci_pagamento.html', socio=socio, current_year=current_year, valor_restante=formatar_moeda(valor_restante))

            if valor_pago + valor_pago_novo > aci_valor_ano.valor:
                flash('O valor pago excede o valor esperado da ACI!', 'error')
                return render_template('cadastrar_aci_pagamento.html', socio=socio, current_year=current_year, valor_restante=formatar_moeda(valor_restante))

            pagamento = AciPagamento(
                id_socio=socio.id,
                id_usuario=current_user.id,
                ano=current_year,
                valor_pago=valor_pago_novo,
                data_pagamento=data_pagamento
            )
            db.session.add(pagamento)
            db.session.commit()
            flash('Pagamento ACI registrado com sucesso!', 'success')
            return redirect(url_for('detalhes_socio', id_socio=socio.id))
        except ValueError:
            flash('Formato de data ou valor inválido!', 'error')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao registrar pagamento ACI: {str(e)}', 'error')

    return render_template('cadastrar_aci_pagamento.html', socio=socio, current_year=current_year, valor_restante=formatar_moeda(valor_restante))

@app.route('/excluir_aci_pagamento/<int:id>', methods=['POST'])
@login_required
def excluir_aci_pagamento(id):
    pagamento = AciPagamento.query.filter_by(id=id, id_usuario=current_user.id).first_or_404()
    id_socio = pagamento.id_socio
    try:
        db.session.delete(pagamento)
        db.session.commit()
        flash('Pagamento ACI excluído com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir pagamento ACI: {str(e)}', 'error')
    return redirect(url_for('detalhes_socio', id_socio=id_socio))


@app.route('/limpar_todos_pagamentos', methods=['POST'])
@login_required
def limpar_todos_pagamentos():
    try:
        # Delete all mensalidades for the current user
        Mensalidade.query.filter_by(id_usuario=current_user.id).delete()
        # Delete all ACI pagamentos for the current user
        AciPagamento.query.filter_by(id_usuario=current_user.id).delete()
        db.session.commit()
        flash('Todos os pagamentos foram limpos com sucesso.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao limpar pagamentos: {str(e)}', 'error')
    return redirect(url_for('dashboard'))



# Support Routes
@app.route('/suporte', methods=['GET', 'POST'])
@login_required
def suporte():
    if request.method == 'POST':
        mensagem = request.form.get('mensagem')
        if not mensagem or len(mensagem.strip()) == 0:
            flash('A mensagem não pode estar vazia.', 'error')
            return redirect(url_for('suporte'))
        try:
            nova_mensagem = SuporteMensagem(
                id_usuario=current_user.id,
                mensagem=mensagem,
                data_envio=date.today()
            )
            db.session.add(nova_mensagem)
            db.session.commit()
            flash('Mensagem enviada com sucesso!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao enviar mensagem: {str(e)}', 'error')
        return redirect(url_for('suporte'))
    
    config = Configuracao.query.filter_by(id_usuario=current_user.id).first()
    user_mensagens = SuporteMensagem.query.filter_by(id_usuario=current_user.id)\
        .options(joinedload(SuporteMensagem.usuario), joinedload(SuporteMensagem.usuario_resposta))\
        .order_by(SuporteMensagem.data_envio.desc()).all()
    if config and config.sinodal == 'Sim':
        mensagens = SuporteMensagem.query\
            .options(joinedload(SuporteMensagem.usuario), joinedload(SuporteMensagem.usuario_resposta))\
            .order_by(SuporteMensagem.data_envio.desc()).all()
    else:
        mensagens = user_mensagens
    return render_template('suporte.html', config=config, mensagens=mensagens, user_mensagens=user_mensagens)

@app.route('/admin_suporte', methods=['GET', 'POST'])
@login_required
def admin_suporte():
    if request.method == 'POST':
        mensagem_id = request.form.get('mensagem_id')
        resposta = request.form.get('resposta')
        if not mensagem_id or not resposta or len(resposta.strip()) == 0:
            flash('Resposta inválida ou mensagem não encontrada.', 'error')
            return redirect(url_for('admin_suporte'))
        try:
            mensagem = SuporteMensagem.query.get(int(mensagem_id))
            if not mensagem:
                flash('Mensagem não encontrada.', 'error')
                return redirect(url_for('admin_suporte'))
            mensagem.resposta = resposta
            mensagem.data_resposta = date.today()
            mensagem.id_usuario_resposta = current_user.id
            db.session.commit()
            flash('Resposta enviada com sucesso!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao enviar resposta: {str(e)}', 'error')
        return redirect(url_for('admin_suporte'))
    
    mensagens = SuporteMensagem.query\
        .options(joinedload(SuporteMensagem.usuario), joinedload(SuporteMensagem.usuario_resposta))\
        .order_by(SuporteMensagem.data_envio.desc()).all()
    return render_template('admin_suporte.html', mensagens=mensagens)

@app.route('/suporte/delete/<int:mensagem_id>', methods=['POST'])
@login_required
def suporte_delete(mensagem_id):
    try:
        mensagem = SuporteMensagem.query.get(mensagem_id)
        if not mensagem:
            flash('Mensagem não encontrada.', 'error')
            return redirect(url_for('suporte'))
        if mensagem.id_usuario != current_user.id:
            flash('Você não tem permissão para excluir esta mensagem.', 'error')
            return redirect(url_for('suporte'))
        db.session.delete(mensagem)
        db.session.commit()
        flash('Mensagem excluída com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir mensagem: {str(e)}', 'error')
    return redirect(url_for('suporte'))



def atualizar_socios_usuario():
    configuracao = Configuracao.query.filter_by(id_usuario=current_user.id).first()

    if not configuracao:
        return  # Nenhuma configuração encontrada para este usuário

    if configuracao.gestor == 'Sim':
        # 🔸 Para gestores

        # 🔹 Buscar subordinados diretos (admin = current_user.id)
        subordinados_diretos = (
            db.session.query(Configuracao)
            .join(Usuario, Configuracao.id_usuario == Usuario.id)
            .filter(
                Configuracao.admin == current_user.id,
                Usuario.is_active == 1
            )
            .all()
        )

        ids_subordinados_diretos = [s.id_usuario for s in subordinados_diretos]

        # 🔹 Buscar subordinados indiretos (admin nos subordinados diretos)
        subordinados_indiretos = (
            db.session.query(Configuracao)
            .join(Usuario, Configuracao.id_usuario == Usuario.id)
            .filter(
                Configuracao.admin.in_(ids_subordinados_diretos),
                Usuario.is_active == 1
            )
            .all()
        )

        # 🔸 Atualiza
        configuracao.socios_ativos = len(subordinados_diretos)
        configuracao.socios_cooperadores = len(subordinados_indiretos)

    else:
        # 🔹 Para não gestores

        # Contagem dos sócios 'Ativo'
        ativos = Socio.query.filter_by(id_usuario=current_user.id, tipo='Ativo').count()

        # Contagem dos sócios 'Cooperador'
        cooperadores = Socio.query.filter_by(id_usuario=current_user.id, tipo='Cooperador').count()

        # Atualiza
        configuracao.socios_ativos = ativos
        configuracao.socios_cooperadores = cooperadores

    db.session.commit()


@app.route('/admin_panel')
def admin_panel():
    return render_template('admin_panel.html')

@app.route('/arquivos/<pasta>')
@login_required
def listar_arquivos(pasta):
    if pasta not in ['uploads', 'relatorios']:
        flash("Pasta inválida!", "danger")
        return redirect(url_for('index'))

    pasta_path = os.path.join('/mnt/data', pasta)
    arquivos = []

    for nome in os.listdir(pasta_path):
        caminho = os.path.join(pasta_path, nome)
        if os.path.isfile(caminho):
            arquivos.append({
                'nome': nome,
                'tamanho_kb': os.path.getsize(caminho) // 1024,
                'data_modificacao': os.path.getmtime(caminho)
            })

    return render_template('arquivos.html', arquivos=arquivos, pasta=pasta)


@app.route('/excluir_arquivo/<pasta>/<nome>')
@login_required
def excluir_arquivo(pasta, nome):
    if pasta not in ['uploads', 'relatorios']:
        flash("Pasta inválida!", "danger")
        return redirect(url_for('index'))

    caminho = os.path.join('/mnt/data', pasta, nome)
    if os.path.exists(caminho):
        os.remove(caminho)
        flash(f"Arquivo '{nome}' excluído com sucesso!", "success")
    else:
        flash("Arquivo não encontrado!", "danger")

    return redirect(url_for('listar_arquivos', pasta=pasta))


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
