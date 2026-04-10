# 🔧 Configuração de Variáveis de Ambiente

## No Railway, vai a: Service → Variables → Add Variable

### Obrigatórias
```
PORT=8080
```

### Chatbot IA (Anthropic)
1. Vai a https://console.anthropic.com
2. Cria uma API Key
3. Adiciona no Railway:
```
ANTHROPIC_API_KEY=sk-ant-...
```

### Login com Google OAuth
1. Vai a https://console.cloud.google.com
2. Cria um projeto novo
3. Ativa "Google+ API" ou "Google Identity"
4. Vai a "Credentials" → "Create Credentials" → "OAuth 2.0 Client ID"
5. Tipo: "Web application"
6. Authorized redirect URIs: https://SEU-DOMINIO.up.railway.app/auth/google/callback
7. Copia o Client ID e Client Secret
8. Adiciona no Railway:
```
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxx
GOOGLE_REDIRECT_URI=https://SEU-DOMINIO.up.railway.app/auth/google/callback
```

### Admin (URL secreta)
O painel admin está escondido numa URL secreta.
Por defeito é: /gestao-hm-2024
Para mudar, edita a linha no server.py:
```python
ADMIN_SECRET_PATH = "/gestao-hm-2024"
```
Muda para algo que só tu sabes, ex: /painel-henriqueta-xk29

### Senha Admin
Já está definida como: Henriqueta2011
Para mudar, edita no server.py:
```python
ADMIN_PASSWORD = "NovaS3nha!"
```

## Resumo do que o site tem

✅ Site público bonito com animações
✅ Secções: Hero, Sobre, Serviços, Agendar, Avaliações, Chatbot
✅ Login com Google OAuth
✅ Agendamentos (nome, morada, criança, idade, data, hora)
✅ Chatbot IA com escalação para funcionário
✅ Avaliações com aprovação pelo admin
✅ Painel admin completo e escondido
✅ Gestão de clientes, marcações, avaliações, funcionários
✅ Rate limiting (60 pedidos/minuto por IP)
✅ Sem dependências externas
