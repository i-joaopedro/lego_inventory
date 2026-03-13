# LEGO Inventory — Sistema de Gestão de Kits Educacionais

Sistema web para controle e conferência de kits LEGO em escolas de robótica educacional.

## Funcionalidades

- **3 níveis de acesso:** Admin, Pedagogo e Auxiliar
- Catálogo de peças com imagens
- Modelos de kit com composição configurável
- Conferência de kits por peça com histórico
- Gráfico de evolução de saúde por kit
- Geração de etiquetas QR Code para impressão
- Exportação de relatórios em PDF
- CSRF protection em todos os formulários
- Senhas criptografadas com bcrypt
- Logs de auditoria (login, conferências)
- Limite de upload de 5 MB e validação de extensão

## Instalação

```bash
# 1. Clone ou descompacte o projeto
cd lego_inventory

# 2. Crie o ambiente virtual
python -m venv venv
source venv/bin/activate        # Linux/Mac
venv\Scripts\activate           # Windows

# 3. Instale as dependências
pip install -r requirements.txt

# 4. Configure as variáveis de ambiente
cp .env.example .env
# Edite o .env com valores reais (especialmente SECRET_KEY e ADMIN_PASSWORD)

# 5. Crie o banco e o admin
python create_admin.py

# 6. Inicie o servidor
python app.py
```

Acesse: http://localhost:5000

## Estrutura de Roles

| Role      | Acesso                                              |
|-----------|-----------------------------------------------------|
| admin     | Tudo: cadastros, usuários, relatórios, conferências |
| pedagogo  | Visualização e relatórios das escolas atribuídas    |
| auxiliar  | Conferência e relatório somente da sua escola       |

## Segurança

- `SECRET_KEY` deve ser uma string aleatória de 32+ bytes em produção
- Nunca commitar o arquivo `.env`
- Em produção, usar HTTPS (Nginx + Gunicorn recomendado)
- Debug mode deve estar `false` em produção

## Variáveis de Ambiente

| Variável        | Descrição                        | Padrão                |
|-----------------|----------------------------------|-----------------------|
| `SECRET_KEY`    | Chave de sessão Flask             | `os.urandom(32)`      |
| `DATABASE_URL`  | URL do banco de dados            | `sqlite:///lego_pro.db` |
| `ADMIN_PASSWORD`| Senha do admin na 1ª init         | `Troque@isso123`      |
| `FLASK_DEBUG`   | Modo debug                       | `false`               |
