FROM python:3.10-slim

# Instala as dependências do sistema e o Google Chrome (Necessário para o Selenium)
RUN apt-get update && apt-get install -y \
    wget gnupg2 apt-transport-https ca-certificates \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && sh -c 'echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list' \
    && apt-get update && apt-get install -y google-chrome-stable \
    && apt-get clean

# Define o diretório de trabalho
WORKDIR /app

# Copia os requirements e instala
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o projeto
COPY . .

# Expõe a porta do nosso painel Flask
EXPOSE 5000

# O comando principal agora é rodar o Painel, e ele vai rodar os outros 3!
CMD ["python", "painel.py"]