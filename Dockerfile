# Etapa base
FROM python:3.11-slim

# Instala dependências do sistema
RUN apt-get update && apt-get install -y \
    poppler-utils \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Cria diretório do app
WORKDIR /app

# Copia os arquivos
COPY . /app

# Instala dependências Python
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Expõe a porta padrão
EXPOSE 8080

# Variável para o Flask
ENV PORT=8080

# Comando para iniciar o app
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]
