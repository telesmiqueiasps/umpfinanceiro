from flask import Flask, send_from_directory, render_template, g, request, redirect, url_for, flash, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash
from models import db, Configuracao, Financeiro, Lancamento, SaldoFinal, Usuario
from datetime import datetime
import os, sqlite3, locale
from io import BytesIO
from fpdf import FPDF
from PIL import Image
from sqlalchemy import func, extract
from pdf2image import convert_from_path
from werkzeug.utils import secure_filename
import random
import re
import requests
from dotenv import load_dotenv
from collections import defaultdict




app = Flask(__name__)
app.secret_key = 'chave_secreta_ump_financeiro'
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.abspath('instance/database.db')}?timeout=10"

UPLOAD_FOLDER = 'uploads/'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db.init_app(app)

# Configuração do Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"  # Quando não logado, irá redirecionar para a página de login


# Criar o banco de dados
with app.app_context():
    db.create_all()




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
        
        if usuario and usuario.senha == senha:  # Verifique a senha de forma segura com hash em produção
            login_user(usuario)
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


# Configura o locale para moeda brasileira
locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')

@app.route('/')
@login_required
def index():
    config = Configuracao.query.filter_by(id_usuario=current_user.id).first()
    
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
    outras_receitas = db.session.query(db.func.sum(Lancamento.valor)).filter(Lancamento.tipo == "Outras Receitas").scalar() or 0
    aci_recebida = db.session.query(db.func.sum(Lancamento.valor)).filter(Lancamento.tipo == "ACI Recebida").scalar() or 0
    outras_despesas = db.session.query(db.func.sum(Lancamento.valor)).filter(Lancamento.tipo == "Outras Despesas").scalar() or 0
    aci_enviada = db.session.query(db.func.sum(Lancamento.valor)).filter(Lancamento.tipo == "ACI Enviada").scalar() or 0

    # Totais de receitas e despesas
    receitas = outras_receitas + aci_recebida
    despesas = outras_despesas + aci_enviada
    saldo_final = (config.saldo_inicial or 0) + receitas - despesas

    # Formatar valores para exibição
    saldo_formatado = locale.currency(config.saldo_inicial or 0, grouping=True)
    receitas_formatadas = locale.currency(receitas, grouping=True)
    despesas_formatadas = locale.currency(despesas, grouping=True)
    saldo_final_formatado = locale.currency(saldo_final, grouping=True)
    outras_receitas_formatadas = locale.currency(outras_receitas, grouping=True)
    aci_recebida_formatada = locale.currency(aci_recebida, grouping=True)
    outras_despesas_formatadas = locale.currency(outras_despesas, grouping=True)
    aci_enviada_formatada = locale.currency(aci_enviada, grouping=True)

    return render_template(
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
        ano=config.ano_vigente
    )



@app.route('/configuracoes', methods=['GET', 'POST'])
@login_required
def configuracoes():
    # Define o locale para formato brasileiro
    locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')

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
        config.socios_ativos = request.form['socios_ativos']
        config.socios_cooperadores = request.form['socios_cooperadores']
        config.tesoureiro_responsavel = request.form['tesoureiro_responsavel']
        config.email = email  # Atualiza o campo de e-mail

        # Converte o saldo para float antes de salvar no banco
        try:
            saldo_inicial = float(request.form['saldo_inicial'].replace('.', '').replace(',', '.'))
        except ValueError:
            saldo_inicial = 0.0

        config.saldo_inicial = saldo_inicial

        # Atualiza o ano na tabela saldo_final para o usuário logado
        SaldoFinal.query.filter_by(id_usuario=current_user.id).update({"ano": config.ano_vigente})

        # Salva as mudanças no banco
        db.session.commit()

        # Recalcula os saldos finais
        recalcular_saldos_finais()

        flash('Configurações salvas com sucesso!', 'success')
        return redirect(url_for('configuracoes'))  # Redireciona para evitar reenvio do formulário

    # Formatar saldo_inicial para exibição no template
    saldo_formatado = locale.currency(config.saldo_inicial, grouping=True) if config else 'R$ 0,00'

    return render_template('configuracoes.html', config=config, saldo_formatado=saldo_formatado)


# Função para verificar se o e-mail já está registrado
def verificar_email_existente(email, id_usuario):
    # Verifica se o e-mail já existe em outra configuração, mas não para o mesmo usuário
    config_existente = Configuracao.query.filter_by(email=email).first()
    if config_existente and config_existente.id_usuario != id_usuario:
        return True  # E-mail já registrado para outro usuário
    return False



# Função para obter o saldo inicial do mês
def obter_saldo_inicial(mes, ano):
    # Obtém o ano vigente da tabela 'configuracoes' para o usuário logado
    ano_vigente = db.session.query(Configuracao.ano_vigente).filter_by(id_usuario=current_user.id).scalar()
    
    if not ano_vigente:
        return 0  # Se não houver configuração, assume saldo 0

    # Usa o ano passado na função apenas se for o mesmo do ano vigente
    if ano != ano_vigente:
        ano = ano_vigente  # Sempre usar o ano vigente salvo no banco

    # Para o mês de janeiro, o saldo inicial será o valor da coluna 'saldo_inicial' na tabela 'configuracoes'
    if mes == 1:
        saldo_inicial = db.session.query(Configuracao.saldo_inicial).filter_by(id_usuario=current_user.id).scalar()
        return saldo_inicial if saldo_inicial is not None else 0  # Retorna 0 caso não tenha saldo inicial definido

    # Para os outros meses, o saldo inicial será o saldo final do mês anterior
    saldo_anterior = db.session.query(SaldoFinal.saldo).filter_by(
        mes=mes - 1,
        ano=ano,  # Usa o ano vigente da tabela 'configuracoes'
        id_usuario=current_user.id
    ).scalar()

    return saldo_anterior if saldo_anterior is not None else 0  # Retorna 0 caso não tenha saldo do mês anterior



# Função para calcular o saldo final do mês
def calcular_saldo_final(mes, ano, saldo_inicial):
    # Buscar lançamentos de entradas (Outras Receitas + ACI Recebida)
    entradas = db.session.query(db.func.sum(Lancamento.valor)).filter(
        (Lancamento.tipo == 'Outras Receitas') | (Lancamento.tipo == 'ACI Recebida'),
        db.extract('month', Lancamento.data) == mes,
        db.extract('year', Lancamento.data) == ano,
        Lancamento.id_usuario == current_user.id  # Filtro pelo usuário logado
    ).scalar() or 0

    # Buscar lançamentos de saídas (Outras Despesas + ACI Enviada)
    saidas = db.session.query(db.func.sum(Lancamento.valor)).filter(
        (Lancamento.tipo == 'Outras Despesas') | (Lancamento.tipo == 'ACI Enviada'),
        db.extract('month', Lancamento.data) == mes,
        db.extract('year', Lancamento.data) == ano,
        Lancamento.id_usuario == current_user.id  # Filtro pelo usuário logado
    ).scalar() or 0

    # Calculando o saldo final com base no saldo inicial
    saldo_final = saldo_inicial + entradas - saidas
    return saldo_final



# Salvar saldo final do mês
def salvar_saldo_final(mes, ano, saldo_inicial):
    # Calcular o saldo final (já ajustado para filtrar por id_usuario)
    saldo_final = calcular_saldo_final(mes, ano, saldo_inicial)
    
    # Verificando se já existe um saldo para o mês e usuário
    saldo_existente = db.session.query(SaldoFinal).filter(
        SaldoFinal.mes == mes,
        SaldoFinal.ano == ano,
        SaldoFinal.id_usuario == current_user.id  # Filtro pelo usuário logado
    ).first()

    if saldo_existente:
        # Atualizando o saldo final se já existir
        saldo_existente.saldo = saldo_final
    else:
        # Criando um novo registro caso não exista
        saldo_novo = SaldoFinal(
            mes=mes,
            ano=ano,
            saldo=saldo_final,
            id_usuario=current_user.id  # Associar ao usuário logado
        )
        db.session.add(saldo_novo)
    
    # Comitar as alterações no banco de dados
    db.session.commit()



def atualizar_saldos_iniciais():
    # Obter o saldo inicial da tabela de configurações para o usuário logado
    saldo_inicial = db.session.query(Configuracao.saldo_inicial).filter_by(id_usuario=current_user.id).first()
    if saldo_inicial:
        saldo_inicial = saldo_inicial[0]
    else:
        saldo_inicial = 0  # Caso não haja saldo configurado, considerar 0

    # Atualizar o saldo inicial de todos os meses para o usuário logado
    for mes in range(1, 13):  # Para todos os meses de janeiro a dezembro
        # Verificar se já existe um saldo inicial para o mês e usuário
        saldo_existente = db.session.query(SaldoFinal).filter(
            SaldoFinal.mes == mes,
            SaldoFinal.id_usuario == current_user.id
        ).first()
        
        if saldo_existente:
            # Atualizar o saldo inicial
            saldo_existente.saldo = saldo_inicial
        else:
            # Criar um novo registro com o saldo inicial configurado
            saldo_novo = SaldoFinal(
                mes=mes,
                ano=2025,  # Defina o ano conforme necessário
                saldo=saldo_inicial,
                id_usuario=current_user.id  # Associar ao usuário logado
            )
            db.session.add(saldo_novo)
    
    # Comitar as alterações no banco de dados
    db.session.commit()



def recalcular_saldos_finais():
    # Obter todos os meses e anos disponíveis para os saldos do usuário logado
    meses_anos = db.session.query(SaldoFinal.mes, SaldoFinal.ano).filter(
        SaldoFinal.id_usuario == current_user.id
    ).distinct().all()

    for mes, ano in meses_anos:
        # Obter o saldo inicial para o mês (já ajustado para o usuário logado)
        saldo_inicial = obter_saldo_inicial(mes, ano)

        # Calcular o saldo final para o mês (já ajustado para o usuário logado)
        saldo_final = calcular_saldo_final(mes, ano, saldo_inicial)

        # Verificar se já existe um saldo final para esse mês e usuário
        saldo_existente = db.session.query(SaldoFinal).filter(
            SaldoFinal.mes == mes,
            SaldoFinal.ano == ano,
            SaldoFinal.id_usuario == current_user.id
        ).first()

        if saldo_existente:
            # Atualizar o saldo final existente
            saldo_existente.saldo = saldo_final
        else:
            # Criar um novo registro para o mês
            saldo_novo = SaldoFinal(
                mes=mes,
                ano=ano,
                saldo=saldo_final,
                id_usuario=current_user.id  # Associar ao usuário logado
            )
            db.session.add(saldo_novo)
    
    # Comitar as alterações no banco de dados
    db.session.commit()


@app.route('/mes/<int:mes>/<int:ano>')
@login_required  # Garante que apenas usuários logados acessem essa rota
def mes(mes, ano):
    # Configuração do locale para Brasil
    locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')  

    # Obtém a configuração do usuário logado
    configuracao = Configuracao.query.filter_by(id_usuario=current_user.id).first()
    
    # Usa o ano vigente salvo na configuração, ou o ano atual caso não exista configuração
    ano_vigente = configuracao.ano_vigente if configuracao else datetime.now().year  

    # Garante que a lógica sempre use o ano vigente salvo
    ano = ano_vigente  

    # Obter o saldo inicial corretamente (considerando o ano vigente)
    saldo_inicial = obter_saldo_inicial(mes, ano)

    # Buscar os lançamentos do mês para o usuário logado
    lancamentos = Lancamento.query.filter(
        Lancamento.data.like(f"{ano}-{mes:02d}%"),
        Lancamento.id_usuario == current_user.id
    ).all()

    # Calculando as entradas e saídas
    entradas = sum(l.valor for l in lancamentos if l.tipo in ['Outras Receitas', 'ACI Recebida'])
    saidas = sum(l.valor for l in lancamentos if l.tipo in ['Outras Despesas', 'ACI Enviada'])

    # Calculando o saldo final do mês
    saldo = saldo_inicial + entradas - saidas

    # Formatando os valores para exibição
    saldo_inicial_formatado = locale.currency(saldo_inicial, grouping=True)
    entradas_formatado = locale.currency(entradas, grouping=True)
    saidas_formatado = locale.currency(saidas, grouping=True)
    saldo_formatado = locale.currency(saldo, grouping=True)

    # Garantir que o mês tenha dois dígitos
    mes_formatado = str(mes).zfill(2)

    # Recalcular os saldos finais para garantir atualização correta
    recalcular_saldos_finais()

    # Renderizando a página
    return render_template(
        'mes.html',
        mes=mes_formatado,
        ano=ano,
        saldo_inicial=saldo_inicial_formatado,
        entradas=entradas_formatado,
        saidas=saidas_formatado,
        saldo=saldo_formatado,
        lancamentos=lancamentos,
        ano_vigente=ano_vigente
    )


@app.route('/lancamentos')
def lancamentos():
    # Obtém o ano vigente da configuração do usuário logado
    configuracao = Configuracao.query.filter_by(id_usuario=current_user.id).first()
    ano_atual = configuracao.ano_vigente if configuracao else datetime.now().year  

    return render_template('lancamentos.html', ano=ano_atual)



@app.route('/adicionar_lancamento/<int:mes>', methods=['GET', 'POST'])
@login_required  # Garante que apenas usuários logados acessem essa rota
def adicionar_lancamento(mes):
    configuracao = Configuracao.query.filter_by(id_usuario=current_user.id).first()
    ano = configuracao.ano_vigente if configuracao else datetime.now().year  # Se não houver configuração, usa o ano atual

    if request.method == 'POST':
        data = request.form.get('data')
        tipo = request.form.get('tipo')
        descricao = request.form.get('descricao')
        valor = request.form.get('valor')
        comprovante = None

        if not data or not tipo or not descricao or not valor:
            flash("Erro: Todos os campos devem ser preenchidos.", "danger")
            return redirect(url_for('adicionar_lancamento', mes=mes))

        try:
            data = datetime.strptime(data, '%Y-%m-%d').date()
            valor = float(valor)
        except ValueError:
            flash("Erro: Data ou valor inválidos.", "danger")
            return redirect(url_for('adicionar_lancamento', mes=mes))

        # Verificando o arquivo de comprovante e salvando no diretório apropriado
        if 'comprovante' in request.files:
            file = request.files['comprovante']

            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)

                # Se for PDF, converte para imagem
                if filename.lower().endswith('.pdf'):
                    from pdf2image import convert_from_path  # Certifique-se de importar isso
                    images = convert_from_path(file_path)  # Converte todas as páginas do PDF para imagens

                    if images:
                        image_filename = filename.replace('.pdf', '.jpg')
                        image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)

                        # Salva apenas a primeira página do PDF como imagem
                        images[0].save(image_path, 'JPEG')

                        # Remove o PDF original para evitar arquivos desnecessários
                        os.remove(file_path)

                        # Atualiza o caminho do comprovante para a imagem gerada
                        file_path = image_path

                comprovante = file_path  # Atualiza a variável do comprovante

        # Criando o objeto Lancamento e salvando no banco com o id_usuario do usuário logado
        lancamento = Lancamento(
            data=data,
            tipo=tipo,
            descricao=descricao,
            valor=valor,
            comprovante=comprovante,
            id_usuario=current_user.id  # Associar ao usuário logado
        )
        db.session.add(lancamento)
        db.session.commit()

        # Calcular o saldo inicial com base no mês (já ajustado para o usuário logado)
        saldo_inicial = obter_saldo_inicial(mes, ano)

        # Salvar o saldo final após o lançamento (já ajustado para o usuário logado)
        salvar_saldo_final(mes, ano, saldo_inicial)

        # Recalcular todos os saldos finais (já ajustado para o usuário logado)
        recalcular_saldos_finais()

        return redirect(url_for('mes', mes=data.month, ano=data.year))

    return render_template('adicionar_lancamento.html', mes=mes, ano_atual=ano)



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
    uploads_folder = os.path.join(app.root_path, 'uploads')  # Caminho absoluto da pasta uploads
    return send_from_directory(uploads_folder, filename)


@app.route('/excluir_lancamento/<int:id>', methods=['POST'])
@login_required  # Garante que apenas usuários logados acessem essa rota
def excluir_lancamento(id):
    # Obtendo 'mes' e 'ano' do formulário
    mes = request.form.get('mes')
    ano = request.form.get('ano')

    if not mes or not ano:
        flash('Erro: Mês ou ano não informado.', 'danger')
        return redirect(url_for('mes', mes=1, ano=2025))  # Redireciona para um padrão se faltar dados

    mes = int(mes)  # Converte para inteiro
    ano = int(ano)  # Converte para inteiro

    with app.app_context():
        # Busca o lançamento apenas se pertencer ao usuário logado
        lancamento = Lancamento.query.filter_by(
            id=id,
            id_usuario=current_user.id
        ).first()

        if lancamento:
            # Verifica se o lançamento tem um comprovante e tenta excluir o arquivo
            if lancamento.comprovante:
                comprovante_path = lancamento.comprovante

                # Verifica se o arquivo existe antes de tentar excluir
                if os.path.exists(comprovante_path):
                    os.remove(comprovante_path)
                    print(f"Comprovante excluído: {comprovante_path}")
                else:
                    print(f"Arquivo não encontrado: {comprovante_path}")

            # Remove o lançamento do banco de dados
            db.session.delete(lancamento)
            db.session.commit()

            # Verifica o saldo inicial correto (já ajustado para o usuário logado)
            saldo_inicial = obter_saldo_inicial(mes, ano)

            # Salvar o saldo final após a exclusão (já ajustado para o usuário logado)
            salvar_saldo_final(mes, ano, saldo_inicial)

            # Recalcular todos os saldos finais após a exclusão (já ajustado para o usuário logado)
            recalcular_saldos_finais()

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
        return redirect(url_for('mes', mes=1, ano=2025))  # Redireciona para um padrão se faltar dados

    if request.method == 'POST':
        # Converte a string de data para um objeto 'date'
        data_string = request.form['data']
        lancamento.data = datetime.strptime(data_string, '%Y-%m-%d').date()

        lancamento.tipo = request.form['tipo']
        lancamento.descricao = request.form['descricao']
        lancamento.valor = float(request.form['valor'])  # Converte valor para float

        # Verifica o comprovante e salva, se necessário
        if 'comprovante' in request.files:
            file = request.files['comprovante']
            if file and allowed_file(file.filename):
                filename = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
                file.save(filename)
                lancamento.comprovante = filename

        db.session.commit()  # Salva as alterações no banco

        # Após editar o lançamento, recalcular os saldos
        mes = int(mes)  # Converte para inteiro
        ano = int(ano)  # Converte para inteiro

        # Recalcula os saldos finais após a edição (já ajustado para o usuário logado)
        recalcular_saldos_finais()

        flash("Lançamento atualizado com sucesso!", "success")
        # Redireciona para a página do mês, passando 'mes' e 'ano' como parâmetros
        return redirect(url_for('mes', mes=mes, ano=ano))

    return render_template('editar_lancamento.html', lancamento=lancamento, mes=mes, ano=ano)




def formatar_valor(valor):
    return locale.currency(valor, grouping=True)

def dados_relatorio(mes=None):
    dados = []
    saldo_anterior = 0

    # Puxar dados de configuração (cabeçalho) para o usuário logado
    configuracao = Configuracao.query.filter_by(id_usuario=current_user.id).first()
    ano_vigente = configuracao.ano_vigente if configuracao else datetime.now().year  # Se não houver configuração, usa o ano atual

    # Puxar os dados de resumo para o usuário logado
    outras_receitas = float(db.session.query(func.sum(Lancamento.valor)).filter(
        Lancamento.tipo == 'Outras Receitas',
        extract('year', Lancamento.data) == ano_vigente,
        Lancamento.id_usuario == current_user.id
    ).scalar() or 0)

    aci_recebida = float(db.session.query(func.sum(Lancamento.valor)).filter(
        Lancamento.tipo == 'ACI Recebida',
        extract('year', Lancamento.data) == ano_vigente,
        Lancamento.id_usuario == current_user.id
    ).scalar() or 0)

    outras_despesas = float(db.session.query(func.sum(Lancamento.valor)).filter(
        Lancamento.tipo == 'Outras Despesas',
        extract('year', Lancamento.data) == ano_vigente,
        Lancamento.id_usuario == current_user.id
    ).scalar() or 0)

    aci_enviada = float(db.session.query(func.sum(Lancamento.valor)).filter(
        Lancamento.tipo == 'ACI Enviada',
        extract('year', Lancamento.data) == ano_vigente,
        Lancamento.id_usuario == current_user.id
    ).scalar() or 0)

    # Calcular total de receitas e despesas
    total_receitas = outras_receitas + aci_recebida
    total_despesas = outras_despesas + aci_enviada
    saldo_final_ano = (configuracao.saldo_inicial or 0) + total_receitas - total_despesas if configuracao else total_receitas - total_despesas

    meses = range(1, 13) if mes is None else [mes]

    for mes_atual in meses:
        saldo_inicial = saldo_anterior if mes_atual > 1 else float(configuracao.saldo_inicial or 0) if configuracao else 0

        # Consultas para entradas e saídas para o usuário logado
        entradas = float(db.session.query(func.sum(Lancamento.valor)).filter(
            Lancamento.tipo.in_(['Outras Receitas', 'ACI Recebida']),
            extract('month', Lancamento.data) == mes_atual,
            extract('year', Lancamento.data) == ano_vigente,
            Lancamento.id_usuario == current_user.id
        ).scalar() or 0)

        saidas = float(db.session.query(func.sum(Lancamento.valor)).filter(
            Lancamento.tipo.in_(['Outras Despesas', 'ACI Enviada']),
            extract('month', Lancamento.data) == mes_atual,
            extract('year', Lancamento.data) == ano_vigente,
            Lancamento.id_usuario == current_user.id
        ).scalar() or 0)

        saldo_final = saldo_inicial + entradas - saidas
        saldo_anterior = saldo_final

        # Lançamentos do mês para o usuário logado
        lancamentos = Lancamento.query.filter(
            extract('month', Lancamento.data) == mes_atual,
            extract('year', Lancamento.data) == ano_vigente,
            Lancamento.id_usuario == current_user.id
        ).all()

        dados.append({
            'mes': mes_atual,
            'saldo_inicial': saldo_inicial,  # Mantém como float
            'entradas': entradas,
            'saidas': saidas,
            'saldo_final': saldo_final,
            'saldo_final_ano': saldo_final_ano,
            'lancamentos': lancamentos,
            'configuracao': configuracao,  # Adicionando a configuração no dicionário
            'outras_receitas': outras_receitas,
            'aci_recebida': aci_recebida,
            'outras_despesas': outras_despesas,
            'aci_enviada': aci_enviada,
            'total_receitas': total_receitas,
            'total_despesas': total_despesas,
            'ano_vigente': ano_vigente  # Adiciona o ano vigente aos dados
        })

    return dados





@app.route('/relatorio')
@login_required  # Garante que apenas usuários logados acessem essa rota
def relatorio():
    mes = request.args.get('mes', type=int, default=None)
    dados = dados_relatorio(mes)  # Agora busca o ano automaticamente da configuração do usuário
    ano = dados[0]['ano_vigente'] if dados else datetime.now().year  # Obtém o ano vigente da configuração

    return render_template('relatorio.html', dados=dados, ano=ano, mes=mes)


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
    logo_path = os.path.join(app.static_folder, "Logos/Marca_UMP 02.png")
    try:
        pdf.image(logo_path, x=80, y=8, w=50)  # Centraliza a logo no topo
    except:
        pass  # Se a imagem não for encontrada, continua sem erro

    pdf.ln(18)  # Adiciona um espaço abaixo da logo para os títulos

    # Título do relatório centralizado
    pdf.set_text_color(28, 30, 62)  # Azul Escuro
    pdf.set_font("Arial", style='B', size=14)
    pdf.cell(190, 10, txt=f"RELATÓRIO FINANCEIRO {ano}{f' - Mês {mes}' if mes else ''}", ln=True, align='C')
    pdf.ln(0)  # Espaço antes do próximo título

    # Título com Nome da UMP/Federação centralizado
    pdf.set_font("Arial", style='B', size=12)
    campo = f"{dados_config.ump_federacao if hasattr(dados_config, 'ump_federacao') else 'Não definido'} - {dados_config.federacao_sinodo if hasattr(dados_config, 'federacao_sinodo') else 'Não definido'}"
    pdf.cell(190, 10, campo, ln=True, align='C')
    pdf.ln(10)  # Espaço após a linha

    # === Cabeçalho ===
    pdf.set_text_color(255, 255, 255)  # Cor branca
    pdf.set_font("Arial", style='B', size=14)
    pdf.set_fill_color(28, 30, 62)  # Azul
    pdf.cell(190, 8, txt="Informações de Cabeçalho", ln=True, align='C', fill=True)
    pdf.ln(5)

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
    campos = [
        ("UMP/Federação:", dados_config.ump_federacao if hasattr(dados_config, 'ump_federacao') else "Não definido"),
        ("Federação/Sínodo:", dados_config.federacao_sinodo if hasattr(dados_config, 'federacao_sinodo') else "Não definido"),
        ("Ano Vigente:", str(dados_config.ano_vigente if hasattr(dados_config, 'ano_vigente') else "Não definido")),
        ("Sócios Ativos:", str(dados_config.socios_ativos if hasattr(dados_config, 'socios_ativos') else "Não definido")),
        ("Sócios Cooperadores:", str(dados_config.socios_cooperadores if hasattr(dados_config, 'socios_cooperadores') else "Não definido")),
        ("Tesoureiro Responsável:", dados_config.tesoureiro_responsavel if hasattr(dados_config, 'tesoureiro_responsavel') else "Não definido"),
    ]

    for campo, valor in campos:
        pdf.cell(largura_campo, altura_celula, campo, border=1)
        pdf.cell(largura_valor, altura_celula, valor, border=1)
        pdf.ln()

    pdf.ln(5)  # Espaço após o cabeçalho

    # === Resumo Financeiro ===
    pdf.set_text_color(255, 255, 255)  # Cor branca
    pdf.set_font("Arial", style='B', size=14)
    pdf.set_fill_color(28, 30, 62)  # Azul
    pdf.cell(190, 8, txt="Resumo Financeiro", ln=True, align='C', fill=True)
    pdf.ln(5)

    resumo = dados[0] if dados else {}

    pdf.set_text_color(28, 30, 62)  # Azul Escuro
    pdf.set_font("Arial", style='B', size=11)
    pdf.set_fill_color(201, 203, 231)  # Azul Claro
    pdf.cell(largura_campo, altura_celula, "Receitas", border=1, align='C', fill=True)
    pdf.cell(largura_valor, altura_celula, "Despesas", border=1, align='C', fill=True)
    pdf.ln()

    pdf.set_text_color(0, 0, 0)  # Cor preta
    pdf.set_font("Arial", size=11)
    resumo_financeiro = [
        (f"Outras Receitas: R$ {locale.format_string('%.2f', resumo.get('outras_receitas', 0.00), grouping=True)}", 
         f"Outras Despesas: R$ {locale.format_string('%.2f', resumo.get('outras_despesas', 0.00), grouping=True)}"),
        (f"ACI Recebida: R$ {locale.format_string('%.2f', resumo.get('aci_recebida', 0.00), grouping=True)}", 
         f"ACI Enviada: R$ {locale.format_string('%.2f', resumo.get('aci_enviada', 0.00), grouping=True)}")
    ]

    for receita, despesa in resumo_financeiro:
        pdf.cell(largura_campo, altura_celula, receita, border=1)
        pdf.cell(largura_valor, altura_celula, despesa, border=1)
        pdf.ln()

    pdf.ln(5)

    # Linha destacando os totais
    pdf.set_font("Arial", style='B', size=12)
    pdf.set_fill_color(200, 200, 200)  # Cinza claro
    pdf.cell(largura_campo, 10, f"Total de Receitas: R$ {locale.format_string('%.2f', resumo.get('total_receitas', 0.00), grouping=True)}", border=1, fill=True)
    pdf.cell(largura_valor, 10, f"Total de Despesas: R$ {locale.format_string('%.2f', resumo.get('total_despesas', 0.00), grouping=True)}", border=1, fill=True)
    pdf.ln(10)

    # === Assinaturas ===
    pdf.ln(20)  # Maior espaço antes das assinaturas

    # Centralizando as assinaturas
    pdf.set_font("Arial", size=12)

    # Assinatura Tesoureiro
    assinatura_texto = "Assinatura do Tesoureiro"
    largura_assinatura = pdf.get_string_width(assinatura_texto) + 10  # Espaço extra para as linhas
    pdf.set_x((pdf.w - largura_assinatura) / 2)  # Centraliza no eixo X
    pdf.cell(largura_assinatura, 10, assinatura_texto, align='C')
    pdf.line((pdf.w - largura_assinatura) / 2, pdf.get_y() + 3, (pdf.w + largura_assinatura) / 2, pdf.get_y() + 3)  # Linha para assinatura
    pdf.ln(20)  # Espaço entre as assinaturas

    # Assinatura Presidente
    assinatura_texto = "Assinatura do Presidente"
    largura_assinatura = pdf.get_string_width(assinatura_texto) + 10  # Espaço extra para as linhas
    pdf.set_x((pdf.w - largura_assinatura) / 2)  # Centraliza no eixo X
    pdf.cell(largura_assinatura, 10, assinatura_texto, align='C')
    pdf.line((pdf.w - largura_assinatura) / 2, pdf.get_y() + 3, (pdf.w + largura_assinatura) / 2, pdf.get_y() + 3)  # Linha para assinatura
    pdf.ln(30)

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
        pdf.cell(190, 10, txt=f"Mês {d['mes']} - {mes_nome} {ano}", ln=True, align='C')
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
        pdf.cell(55, 10, f"R$ {locale.format_string('%.2f', d['saldo_inicial'], grouping=True)}", border=1, align='C')
        pdf.cell(40, 10, f"R$ {locale.format_string('%.2f', d['entradas'], grouping=True)}", border=1, align='C')
        pdf.cell(40, 10, f"R$ {locale.format_string('%.2f', d['saidas'], grouping=True)}", border=1, align='C')
        pdf.cell(55, 10, f"R$ {locale.format_string('%.2f', d['saldo_final'], grouping=True)}", border=1, align='C')
        pdf.ln(15)

        # Tabela de lançamentos
        pdf.set_font("Arial", style='B', size=10)
        pdf.set_fill_color(200, 200, 200)  # Cinza claro
        pdf.cell(35, 10, "Data", border=1, align='C', fill=True)
        pdf.cell(35, 10, "Tipo", border=1, align='C', fill=True)
        pdf.cell(65, 10, "Descrição", border=1, align='C', fill=True)
        pdf.cell(35, 10, "Valor", border=1, align='C', fill=True)
        pdf.cell(20, 10, "Cód.", border=1, align='C', fill=True)
        pdf.ln()

        pdf.set_font("Arial", size=10)
        for lanc in d['lancamentos']:
            pdf.cell(35, 10, txt=lanc.data.strftime('%d/%m/%Y'), border=1, align='C')
            pdf.cell(35, 10, txt=lanc.tipo, border=1, align='C')
            pdf.cell(65, 10, txt=lanc.descricao, border=1, align='C')
            pdf.cell(35, 10, txt=f"R$ {locale.format_string('%.2f', lanc.valor, grouping=True)}", border=1, align='C')
            pdf.cell(20, 10, txt=str(lanc.id), border=1, align='C')
            pdf.ln()

        pdf.ln(5)  # Espaço entre meses

    # Geração e envio do arquivo PDF
    pdf_file = f"relatorio_{ano}_id_usuario_{current_user.id}.pdf"
    relatorios_dir = os.path.join(os.path.dirname(__file__), 'relatorios')  # Caminho para a pasta 'relatorios' no diretório raiz
    os.makedirs(relatorios_dir, exist_ok=True)  # Cria a pasta 'relatorios' se não existir
    pdf_path = os.path.join(relatorios_dir, pdf_file)  # Caminho completo do arquivo
    pdf.output(pdf_path)  # Salva o PDF na pasta 'relatorios'
    return send_file(pdf_path, as_attachment=True)  # Envia o arquivo ao usuário



def buscar_lancamentos(ano=None, mes=None):
    """Retorna os lançamentos filtrados por ano, mês e usuário logado."""
    query = Lancamento.query  # Começa a consulta no banco

    # Filtra sempre pelo usuário logado
    query = query.filter(Lancamento.id_usuario == current_user.id)

    if ano:
        query = query.filter(extract('year', Lancamento.data) == ano)  # Filtra pelo ano

    if mes:
        query = query.filter(extract('month', Lancamento.data) == mes)  # Filtra pelo mês

    return query.all()  # Retorna os lançamentos filtrados



@app.route('/exportar-comprovantes')
@login_required  # Garante que apenas usuários logados acessem essa rota
def exportar_comprovantes():
    ano = request.args.get('ano', type=int, default=None)
    mes = request.args.get('mes', type=int, default=None)
    dados = dados_relatorio(ano, mes)  # Já ajustado para o usuário logado

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
        pdf.cell(15, 10, str(lanc.id), border=1, align='C')
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
            comprovante_path = os.path.join(app.config['UPLOAD_FOLDER'], lanc.comprovante) if not lanc.comprovante.startswith('uploads/') else lanc.comprovante

            print(f"Comprovante Path: {comprovante_path}")  # Para debug

            # Verifique se o arquivo existe e é uma imagem
            if os.path.exists(comprovante_path):
                file_extension = comprovante_path.lower().split('.')[-1]
                if file_extension in ['jpg', 'jpeg', 'png']:
                    try:
                        pdf.add_page()
                        pdf.set_font("Arial", style='B', size=12)
                        pdf.cell(190, 10, f"Comprovante - Cód. {lanc.id}", ln=True, align='C')
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
                    pdf.cell(190, 10, f"Comprovante - ID {lanc.id} (Formato de arquivo não suportado)", ln=True, align='C')
                    pdf.ln(5)
                    pdf.cell(190, 10, f"Comprovante: {comprovante_path} - Não é imagem", ln=True, align='C')
            else:
                pdf.add_page()
                pdf.set_font("Arial", style='B', size=12)
                pdf.cell(190, 10, f"Comprovante - ID {lanc.id} (Arquivo não encontrado)", ln=True, align='C')

    # Salvar e enviar o PDF
    pdf_path = f"relatorios/comprovantes_{ano}_id_usuario_{current_user.id}.pdf"
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
    relatorios_dir = os.path.join(os.path.dirname(__file__), 'relatorios')  # Caminho para a pasta relatorios
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
    relatorios_dir = os.path.join(os.path.dirname(__file__), 'relatorios')
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
    relatorios_dir = os.path.join(os.path.dirname(__file__), 'relatorios')  # Caminho para a pasta relatorios
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
    relatorios_dir = os.path.join(os.path.dirname(__file__), 'relatorios')
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
    """Carrega os administradores e seus usuários do banco de dados."""
    # Buscar administradores específicos, se possível, ao invés de carregar todos os registros
    administradores = defaultdict(list)  # Alterado para usar list ao invés de set
    registros = Configuracao.query.all()  # Pode ser otimizado se você precisar de filtros aqui
    
    for registro in registros:
        administradores[registro.admin].append((registro.id_usuario, registro.ump_federacao))
    
    return {admin: usuarios for admin, usuarios in administradores.items()}

def get_usuarios_autorizados():
    """Retorna a lista de usuários (id e ump_federacao) que o administrador atual pode acessar."""
    administradores = carregar_administradores()
    return administradores.get(current_user.id, [])


@app.route('/admin_consultar')
@login_required
def admin_consultar():
    administradores = carregar_administradores()
    # Verificar se o administrador atual está presente no dicionário de administradores
    if current_user.id not in administradores:
        flash("Você não tem permissão para acessar esta página.", "danger")
        return redirect(url_for("index"))
    
    # Se o administrador for válido, passar a lista de usuários autorizados para o template
    usuarios_autorizados = administradores.get(current_user.id, [])
    
    return render_template('admin_consultar.html', usuarios_autorizados=usuarios_autorizados)

@app.route('/admin/buscar_relatorio', methods=['GET', 'POST'])
@login_required
def admin_buscar_relatorio():
    administradores = carregar_administradores()
    if current_user.id not in administradores:
        flash("Você não tem permissão para acessar esta página.", "danger")
        return redirect(url_for("index"))

    relatorios_dir = os.path.join(os.path.dirname(__file__), 'relatorios')
    relatorio_encontrado = None
    usuario_selecionado = None  

    # Obtenha os IDs dos usuários autorizados
    usuarios_autorizados_ids = administradores.get(current_user.id, set())

    # Corrigir a consulta para obter apenas os IDs dos usuários
    usuarios_autorizados = db.session.query(Configuracao.id_usuario, Configuracao.ump_federacao) \
        .filter(Configuracao.id_usuario.in_([usuario[0] for usuario in administradores.get(current_user.id, set())])) \
        .all()


    # Garantir que ump_federacao tenha um valor válido
    usuarios_autorizados = [
        {"id_usuario": usuario.id_usuario, 
         "ump_federacao": usuario.ump_federacao if usuario.ump_federacao else "Nome não disponível"}
        for usuario in usuarios_autorizados
    ]

    if request.method == 'POST':
        ano = request.form.get('ano', type=int)
        usuario_id = request.form.get('usuario_id', type=int)

        if not ano or not usuario_id:
            flash('Por favor, selecione um ano e um usuário.', 'danger')
            return redirect(url_for('admin_buscar_relatorio'))

        if usuario_id not in usuarios_autorizados_ids:
            flash('Você não tem permissão para acessar relatórios deste usuário.', 'danger')
            return redirect(url_for('admin_buscar_relatorio'))

        relatorio_nome = f"relatorio_{ano}_id_usuario_{usuario_id}.pdf"
        relatorio_path = os.path.join(relatorios_dir, relatorio_nome)

        if os.path.exists(relatorio_path):
            relatorio_encontrado = relatorio_nome
        else:
            flash(f'Relatório para o ano {ano} do usuário {usuario_id} não encontrado.', 'warning')

        usuario_selecionado = usuario_id  

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

    relatorios_dir = os.path.join(os.path.dirname(__file__), 'relatorios')
    relatorio_path = os.path.join(relatorios_dir, filename)

    # Pega o ID do usuário dentro do nome do arquivo
    try:
        usuario_id = int(filename.split("_id_usuario_")[1].split(".")[0])
    except (IndexError, ValueError):
        flash("Nome de arquivo inválido.", "danger")
        return redirect(url_for('admin_consultar'))

    if usuario_id not in [usuario.id for usuario in usuarios_autorizados]:
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
    if current_user.id not in administradores:
        flash("Você não tem permissão para acessar esta página.", "danger")
        return redirect(url_for("index"))

    relatorios_dir = os.path.join(os.path.dirname(__file__), 'relatorios')
    relatorio_encontrado = None
    usuario_selecionado = None  

    # Obtenha os IDs dos usuários autorizados
    usuarios_autorizados_ids = administradores.get(current_user.id, set())

    # Corrigir a consulta para obter apenas os IDs dos usuários
    usuarios_autorizados = db.session.query(Configuracao.id_usuario, Configuracao.ump_federacao) \
        .filter(Configuracao.id_usuario.in_([usuario[0] for usuario in administradores.get(current_user.id, set())])) \
        .all()


    # Garantir que ump_federacao tenha um valor válido
    usuarios_autorizados = [
        {"id_usuario": usuario.id_usuario, 
         "ump_federacao": usuario.ump_federacao if usuario.ump_federacao else "Nome não disponível"}
        for usuario in usuarios_autorizados
    ]

    if request.method == 'POST':
        ano = request.form.get('ano', type=int)
        usuario_id = request.form.get('usuario_id', type=int)

        if not ano or not usuario_id:
            flash('Por favor, selecione um ano e um usuário.', 'danger')
            return redirect(url_for('admin_buscar_comprovantes'))

        if usuario_id not in usuarios_autorizados_ids:
            flash('Você não tem permissão para acessar comprovantes deste usuário.', 'danger')
            return redirect(url_for('admin_buscar_comprovantes'))

        relatorio_nome = f"comprovantes_{ano}_id_usuario_{usuario_id}.pdf"
        relatorio_path = os.path.join(relatorios_dir, relatorio_nome)

        if os.path.exists(relatorio_path):
            relatorio_encontrado = relatorio_nome
        else:
            flash(f'Comprovantes para o ano {ano} do usuário {usuario_id} não encontrado.', 'warning')

        usuario_selecionado = usuario_id  

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

    relatorios_dir = os.path.join(os.path.dirname(__file__), 'relatorios')
    relatorio_path = os.path.join(relatorios_dir, filename)

    # Pega o ID do usuário dentro do nome do arquivo
    try:
        usuario_id = int(filename.split("_id_usuario_")[1].split(".")[0])
    except (IndexError, ValueError):
        flash("Nome de arquivo inválido.", "danger")
        return redirect(url_for('admin_consultar'))

    if usuario_id not in [usuario.id for usuario in usuarios_autorizados]:
        flash("Você não tem permissão para visualizar este relatório.", "danger")
        return redirect(url_for('admin_consultar'))

    if os.path.exists(relatorio_path):
        return send_file(relatorio_path, mimetype='application/pdf')
    else:
        flash('Comprovantes não encontrados.', 'danger')
        return redirect(url_for('admin_consultar'))





@app.route('/excluir_todos_lancamentos', methods=['GET', 'POST'])
@login_required  # Garante que apenas usuários logados acessem essa rota
def excluir_todos_lancamentos():
    if request.method == 'POST':  # Verifica se é uma requisição POST
        with app.app_context():
            # Busca todos os lançamentos do usuário logado
            lancamentos = Lancamento.query.filter_by(id_usuario=current_user.id).all()

            if not lancamentos:
                flash('Nenhum lançamento encontrado para exclusão.', 'warning')
                return redirect(url_for('excluir_todos_lancamentos'))

            # Excluir os comprovantes associados (se existirem)
            for lancamento in lancamentos:
                if lancamento.comprovante:
                    comprovante_path = lancamento.comprovante

                    if os.path.exists(comprovante_path):
                        os.remove(comprovante_path)
                        print(f"Comprovante excluído: {comprovante_path}")
                    else:
                        print(f"Arquivo não encontrado: {comprovante_path}")

                # Remove cada lançamento do banco de dados
                db.session.delete(lancamento)

            # Commit das alterações no banco de dados
            db.session.commit()

            # Recalcular os saldos finais após a exclusão em todos os meses do ano vigente
            configuracao = Configuracao.query.filter_by(id_usuario=current_user.id).first()
            ano_vigente = configuracao.ano_vigente if configuracao else datetime.now().year

            for mes in range(1, 13):
                saldo_inicial = obter_saldo_inicial(mes, ano_vigente)
                salvar_saldo_final(mes, ano_vigente, saldo_inicial)

            # Recalcula os saldos finais novamente para garantir que tudo esteja atualizado
            recalcular_saldos_finais()

            flash('Todos os lançamentos e comprovantes foram excluídos com sucesso!', 'success')

        return redirect(url_for('excluir_todos_lancamentos'))  # Redireciona para a mesma página de exclusão após a operação

    return render_template('excluir_lancamentos.html')  # Renderiza a página de confirmação de exclusão



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
            ump_federacao='UMP Federação',
            federacao_sinodo='Nome do Sinodo',
            ano_vigente=datetime.now().year,
            socios_ativos=0,
            socios_cooperadores=0,
            tesoureiro_responsavel='Nome do Tesoureiro',
            saldo_inicial=0.0,
            email=email
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

        return redirect(url_for('cadastro'))  # Redireciona para a mesma página

    return render_template('cadastro.html')


if __name__ == '__main__':
    app.run(debug=True)
