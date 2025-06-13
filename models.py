from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import date



db = SQLAlchemy()

class Configuracao(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_usuario = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    admin = db.Column(db.Integer, nullable=False)
    gestor = db.Column(db.String(100), nullable=False)
    sinodal = db.Column(db.String(100), nullable=False)
    ump_federacao = db.Column(db.String(100), nullable=False)
    federacao_sinodo = db.Column(db.String(100), nullable=False)
    ano_vigente = db.Column(db.Integer, nullable=False)
    socios_ativos = db.Column(db.Integer, nullable=False)
    socios_cooperadores = db.Column(db.Integer, nullable=False)
    tesoureiro_responsavel = db.Column(db.String(100), nullable=False)
    presidente_responsavel = db.Column(db.String(100), nullable=False)
    saldo_inicial = db.Column(db.Float, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    ultimo_id_lancamento = db.Column(db.Integer, default=0)



class Financeiro(db.Model): # Criei essa tabela nem sei pq, por enquanto ainda sem utilização
    __tablename__ = 'financeiro'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_usuario = db.Column(db.Integer, nullable=False)
    data = db.Column(db.Date)  
    tipo = db.Column(db.String(50))
    valor = db.Column(db.Float)

class Lancamento(db.Model):
    __tablename__ = 'lancamento'  

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_usuario = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    id_lancamento = db.Column(db.Integer, nullable=False)
    data = db.Column(db.Date, nullable=False)
    tipo = db.Column(db.String(50), nullable=False)
    descricao = db.Column(db.String(120), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    comprovante = db.Column(db.String(120), nullable=True)

    def __repr__(self):
        return f'<Lancamento {self.id} - {self.descricao}>'

class SaldoFinal(db.Model):
    __tablename__ = 'saldo_final'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_usuario = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    mes = db.Column(db.Integer, nullable=False)
    ano = db.Column(db.Integer, nullable=False)
    saldo = db.Column(db.Float, nullable=False)

    def __init__(self, mes, ano, saldo, id_usuario):
        self.mes = mes
        self.ano = ano
        self.saldo = saldo
        self.id_usuario = id_usuario

class Usuario(db.Model, UserMixin):
    __tablename__ = 'usuario'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    senha = db.Column(db.String(256), nullable=False)  
    is_active = db.Column(db.Integer, nullable=False, default=1) # Aqui usei is active por causa da compatibilidade com Flask-Login

    def verificar_senha(self, senha):
        """Verifica a senha sem hash (comparação direta)"""
        return self.senha == senha

    def set_senha(self, senha):
        """Armazena a senha diretamente"""
        self.senha = senha

    def __init__(self, username, senha, is_active=1):
        self.username = username
        self.senha = senha
        self.is_active = 1 if is_active in [True, 1] else 0


class Socio(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_usuario = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)  # 'Ativo' or 'Cooperador'


class Mensalidade(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_socio = db.Column(db.Integer, db.ForeignKey('socio.id'), nullable=False)
    id_usuario = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    ano = db.Column(db.Integer, nullable=False)
    mes = db.Column(db.Integer, nullable=False)  # 1 to 12
    valor_pago = db.Column(db.Float, nullable=False)
    data_pagamento = db.Column(db.Date, nullable=False, default=date.today)

    __table_args__ = (
        db.UniqueConstraint('id_socio', 'ano', 'mes', name='unique_mensalidade_socio'),
        db.CheckConstraint('mes BETWEEN 1 AND 12', name='check_mes'),
    )


class AciValorAno(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_usuario = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    ano = db.Column(db.Integer, nullable=False)
    valor = db.Column(db.Float, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('id_usuario', 'ano', name='unique_aci_valor_ano'),
    )

class AciPagamento(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_socio = db.Column(db.Integer, db.ForeignKey('socio.id'), nullable=False)
    id_usuario = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    ano = db.Column(db.Integer, nullable=False)
    valor_pago = db.Column(db.Float, nullable=False)
    data_pagamento = db.Column(db.Date, nullable=False, default=date.today) 

class SuporteMensagem(db.Model):
    __tablename__ = 'suporte_mensagem'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_usuario = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    mensagem = db.Column(db.Text, nullable=False)
    data_envio = db.Column(db.Date, nullable=False, default=date.today)
    id_usuario_resposta = db.Column(db.Integer, db.ForeignKey('usuario.id'))
    resposta = db.Column(db.Text)
    data_resposta = db.Column(db.Date)
    usuario = db.relationship('Usuario', foreign_keys=[id_usuario], backref='mensagens_enviadas')
    usuario_resposta = db.relationship('Usuario', foreign_keys=[id_usuario_resposta], backref='mensagens_respondidas')
