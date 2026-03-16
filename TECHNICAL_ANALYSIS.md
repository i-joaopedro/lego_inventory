# Análise Técnica Detalhada do Projeto Inventário LEGO

Este documento fornece uma visão estrutural e funcional completa do sistema de Inventário LEGO, conforme as atualizações recentes.

## 1. Modelos de Dados (SQLAlchemy)

Os modelos estão definidos no `app.py` utilizando o SQLAlchemy para mapeamento objeto-relacional (ORM).

*   **`Usuario`**: Gerencia as credenciais e permissões. Armazena `username`, `password` (em hash), `role` (admin, pedagogo, auxiliar), `escola` vinculada e status de ativação.
*   **`Peca`**: Representa o catálogo de peças individuais. Contém o código oficial LEGO, nome e URL da imagem.
*   **`KitModelo`**: Define a "receita" de um kit. Serve como o modelo mestre (ex: BricQ Motion Prime) ao qual as peças são vinculadas.
*   **`ComposicaoKit`**: Tabela de associação entre `KitModelo` e `Peca`. Define a `quantidade_esperada` de cada peça naquele modelo específico.
*   **`KitUnidade`**: Representa a unidade física existente em uma escola (ex: "Kit #01"). É vinculada a um `KitModelo` e rastreia o `status_atual` (Completo/Incompleto/Pendente).
*   **`Conferencia`**: Registro de uma inspeção realizada em uma `KitUnidade`. Armazena a data, o responsável, observações e o status resultante.
*   **`ConferenciaDetalhe`**: Detalha a contagem de cada peça durante uma `Conferencia` específica, registrando quanto foi encontrado vs. o esperado na época.
*   **`APIToken`**: Gerencia chaves de acesso para integrações externas via API Bearer.
*   **`Escola`**: Centraliza as informações das unidades escolares atendidas pelo sistema.

## 2. Segurança e Controle de Sessão

A segurança é implementada através de várias camadas:

*   **Decorador `login_required(roles)`**: Uma função envolvente personalizada que verifica se o usuário está autenticado e se possui um dos cargos necessários para acessar a rota.
*   **Flask Session**: Utilizada para manter o estado do usuário (`user_id`, `role`, `username`) entre as requisições. A sessão é protegida por uma `SECRET_KEY`.
*   **Hash de Senhas**: As senhas nunca são armazenadas em texto plano, utilizando `werkzeug.security` (`generate_password_hash` e `check_password_hash`).
*   **Proteção CSRF**: O `Flask-WTF` garante que todas as requisições `POST` (formulários e AJAX) contenham um token de segurança para prevenir ataques Cross-Site Request Forgery.
*   **Isolamento de Dados**: Rotas de conferência e dashboards de auxiliares filtram os dados com base na `escola` vinculada ao usuário na sessão.

## 3. Comunicação Rotas <-> Templates

O sistema utiliza o motor de templates **Jinja2**:

*   **Rotas (Flask)**: Processam a lógica de negócios, consultam o banco de dados e passam objetos/variáveis para a função `render_template`.
*   **Templates (HTML)**: Utilizam blocos `{% ... %}` para lógica (como as novas condicionais de permissão no menu) e `{{ ... }}` para exibição de dados.
*   **AJAX**: Implementado na gestão de composição usando a API `fetch`. A rota `/api/composicao/ajustar_quantidade` recebe JSON, atualiza o banco e retorna o novo valor, que o JavaScript injeta no DOM sem recarregar a página.

## 4. Gerador de PDF e QR Code

*   **PDF (ReportLab)**:
    *   Utiliza as funções `_pdf_relatorio_kit` e `_pdf_relatorio_escola`.
    *   O ReportLab constrói o documento dinamicamente em um `BytesIO` (buffer de memória).
    *   Inclui tabelas formatadas (`Table`), estilos de parágrafo e inserção de imagens das peças a partir do sistema de arquivos.
*   **QR Code**:
    *   As etiquetas são geradas dinamicamente via integração com a API `qrserver.com` no front-end.
    *   O URL codificado aponta para a rota de conferência do kit específico (`/conferir/<kit_id>`), permitindo que o auxiliar escaneie o kit físico e abra a tela de contagem instantaneamente.

## 5. Padrão DRY e Código Limpo

O projeto segue o princípio **Don't Repeat Yourself**:
*   Helpers centralizados para cálculo de estatísticas (`_calcular_stats`) e ranking de perdas.
*   Uso de `base.html` para evitar duplicação de estrutura de navegação e scripts comuns.
*   Lógica de permissões centralizada no decorador customizado, evitando verificações manuais repetitivas dentro de cada função de rota.
