
# ===== IMPORTS =====
from notion_client import Client
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
)
import requests
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
PARENT_DATABASE_ID = os.getenv("PARENT_DATABASE_ID")
GROQ_MODEL = os.getenv("GROQ_MODEL")



# ===== INICIALIZAÇÃO =====
notion = Client(auth=NOTION_TOKEN)
memory = {}      # Guarda título, roteiro, dicas por chat_id
subpages = {}    # Guarda ID da página criada por chat_id

# ===== CONFIGS TELEGRAM =====
MAX_CHUNK = 4000  # Máximo por mensagem Telegram
MAX_BLOCK = 1999  # Máximo por bloco Notion
MAX_TOKENS = 3000 # Max tokens para Groq API

# ===== FUNÇÃO PARA ENVIAR PREVIEW LONGO =====
async def enviar_preview(update, texto: str):
    """Divide um texto longo em blocos e envia por partes no Telegram."""
    texto = str(texto)
    for i in range(0, len(texto), MAX_CHUNK):
        await update.message.reply_text(texto[i:i+MAX_CHUNK])

# ===== FUNÇÃO CONVERSAR COM IA =====
async def conversar_ia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envia a mensagem do usuário para a IA e recebe roteiro. Salva apenas a última sugestão."""
    chat_id = update.effective_chat.id
    msg = update.message.text

    if chat_id not in memory:
        memory[chat_id] = {"titulo": "", "roteiro": "", "dicas": ""}

    prompt = f"""
Você é um assistente de criação de Shorts 30-40 segundos. 
O usuário quer que você gere TÍTULO e ROTEIRO em um formato fixo:

<TÍTULO DO VÍDEO>

ROTEIRO (com divisões por segundo):

| Tempo | Texto (narrador) | Visuais / Ações | Dicas de edição |
|-------|-----------------|----------------|----------------|
| 0:00 – 0:03 | ... | ... | ... |
| 0:04 – 0:07 | ... | ... | ... |
...

Mensagem do usuário: {msg}Fornecendo sempre ao final do roteiro, dicas engajadoras, sugestões de memes que podem ser um gancho em certos momentos(Entregar link com a bsuca dos memes, efeitos, animações).
você nunca dara sugestões que são falsas, nada de fake news, a IA pode recomendar um short sobre alguma especulação que esta no mercado nos dias de hoje.
Sempre pesquise afundo sobre o assunto para verificar veracidade. Apontando sempre As suas fontes de buscas
"""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    resposta = ""
    try:
        payload = {
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": MAX_TOKENS
        }

        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload, headers=headers, timeout=30
        )

        if response.status_code != 200:
            resposta = f"❌ Erro na API Groq: {response.status_code} {response.text}"
        else:
            result = response.json()
            resposta = result["choices"][0].get("message", {}).get("content", "")
            if not resposta.strip():
                resposta = "❌ IA não retornou nenhum texto"

    except Exception as e:
        resposta = f"❌ Erro ao chamar API Groq: {str(e)}"

    # salva apenas a última sugestão
    memory[chat_id]["roteiro"] = resposta

    # envia preview longo para Telegram
    await enviar_preview(update, resposta)

# ===== FUNÇÃO SALVAR NO NOTION =====
async def salvar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Salva o roteiro atual no Notion, dividindo em blocos seguros de 2000 caracteres."""
    chat_id = update.effective_chat.id
    if chat_id not in memory:
        await update.message.reply_text("❌ Nenhum vídeo encontrado. Converse com a IA primeiro.")
        return

    conteudo = memory[chat_id]
    # Pega o título completo após o comando
    titulo = update.message.text[len("/salvar "):].strip() or "Novo Vídeo"
    roteiro = conteudo.get("roteiro", "")
    if not roteiro.strip():
        await update.message.reply_text("❌ Nenhum roteiro encontrado para salvar.")
        return

    data_agora = datetime.now().isoformat()

    # Cria a página no Notion
    res = notion.pages.create(
        parent={"database_id": PARENT_DATABASE_ID},
        properties={
            "Título": {"title": [{"text": {"content": titulo}}]},
            "Status": {"status": {"name": "Não iniciada"}},
            "Data da Ideia": {"date": {"start": data_agora}}
        }
    )
    page_id = res["id"]
    subpages[chat_id] = page_id

    # Divide roteiro em blocos seguros
    roteiro_limpo = roteiro.replace('\r\n', '\n')
    inicio = 0
    while inicio < len(roteiro_limpo):
        bloco = roteiro_limpo[inicio:inicio + MAX_BLOCK]
        bloco = bloco[:MAX_BLOCK]
        inicio += MAX_BLOCK

        notion.blocks.children.append(
            page_id,
            children=[{
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": bloco}}]}
            }]
        )

    await update.message.reply_text(f"✅ Roteiro salvo no Notion!\n🎬 Vídeo: {titulo}")

# ===== FUNÇÃO CARREGAR LISTA DE VÍDEOS =====
async def carregar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista os vídeos salvos no Notion."""
    chat_id = update.effective_chat.id
    try:
        query = notion.databases.query(database_id=PARENT_DATABASE_ID)
        resultados = query.get("results", [])
        if not resultados:
            await update.message.reply_text("Nenhum vídeo encontrado no Notion.")
            return

        memory.setdefault(chat_id, {})["lista_videos"] = resultados
        nomes = []
        for i, page in enumerate(resultados, start=1):
            titulo = "(Sem título)"
            for key, val in page["properties"].items():
                if val.get("type") == "title" and val["title"]:
                    titulo = val["title"][0]["text"]["content"]
                    break
            nomes.append(f"{i}. {titulo}")

        await update.message.reply_text("📂 Vídeos no Notion:\n" + "\n".join(nomes))

    except Exception as e:
        import traceback
        await update.message.reply_text(f"❌ Erro ao carregar:\n{traceback.format_exc()[:1500]}")

# ===== FUNÇÃO CARREGAR ROTEIRO POR ÍNDICE =====
async def carregar_roteiro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe o roteiro completo de um vídeo pelo número da lista."""
    chat_id = update.effective_chat.id
    if chat_id not in memory or "lista_videos" not in memory[chat_id]:
        await update.message.reply_text("❌ Use primeiro o comando /carregar para listar os vídeos.")
        return
    if not context.args:
        await update.message.reply_text("❌ Informe o número do vídeo. Exemplo: /carregar_roteiro 1")
        return

    try:
        indice = int(context.args[0]) - 1
    except ValueError:
        await update.message.reply_text("❌ Número inválido. Use: /carregar_roteiro 1")
        return

    lista_videos = memory[chat_id]["lista_videos"]
    if indice < 0 or indice >= len(lista_videos):
        await update.message.reply_text("❌ Número fora da lista.")
        return

    page = lista_videos[indice]
    page_id = page["id"]
    titulo = page["properties"]["Título"]["title"][0]["text"]["content"]

    try:
        blocks = notion.blocks.children.list(page_id)["results"]
        roteiro = []
        for block in blocks:
            if block["type"] == "paragraph":
                text_items = block["paragraph"]["rich_text"]
                if text_items:
                    roteiro.append("".join([t["text"]["content"] for t in text_items]))

        texto_final = f"🎬 {titulo}\n\n" + "\n".join(roteiro)
        await enviar_preview(update, texto_final)

    except Exception as e:
        await update.message.reply_text(f"❌ Erro ao carregar roteiro: {str(e)}")

# ===== FUNÇÃO HELP =====
async def ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe uma mensagem detalhada com todos os comandos disponíveis."""
    texto = """
📌 *Comandos disponíveis:*

/salvar <título> - Salva o último roteiro gerado pela IA no Notion com o título fornecido.  
Exemplo: /salvar Meu Short Incrível

/carregar - Lista todos os vídeos/roteiros salvos no Notion.

/carregar_roteiro <número> - Mostra o roteiro completo do vídeo pelo número na lista.  
Exemplo: /carregar_roteiro 1

/help - Exibe esta mensagem de ajuda.

💡 *Dicas:*  
- Sempre que a IA gerar uma sugestão, apenas a última será salva.  
- Use títulos claros e curtos para facilitar a organização no Notion.  
- Você pode pedir várias sugestões da IA antes de salvar, mas apenas a última será guardada.
"""
    await update.message.reply_text(texto, parse_mode="Markdown")

# ===== BOT TELEGRAM =====
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, conversar_ia))
app.add_handler(CommandHandler("salvar", salvar))
app.add_handler(CommandHandler("carregar", carregar))
app.add_handler(CommandHandler("carregar_roteiro", carregar_roteiro))
app.add_handler(CommandHandler("help", ajuda))

# ===== RODA BOT =====
app.run_polling()
