# -*- coding: utf-8 -*-
"""Generator PDF z dokumentacją projektu (przygotowanie do rozmowy kwalifikacyjnej).

Czyta pliki źródłowe bezpośrednio z repozytorium, więc kod w PDF jest wierny 1:1.
"""
import os
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Preformatted,
    Table, TableStyle, HRFlowable, PageBreak, KeepTogether,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "Dokumentacja_projektu_Discord_Bot.pdf")

# ---------------------------------------------------------------- czcionki
FONTS = "C:/Windows/Fonts"
pdfmetrics.registerFont(TTFont("Arial", f"{FONTS}/arial.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Bold", f"{FONTS}/arialbd.ttf"))
try:
    pdfmetrics.registerFont(TTFont("Arial-Italic", f"{FONTS}/ariali.ttf"))
except Exception:
    pdfmetrics.registerFont(TTFont("Arial-Italic", f"{FONTS}/arial.ttf"))
pdfmetrics.registerFont(TTFont("Consolas", f"{FONTS}/consola.ttf"))
pdfmetrics.registerFont(TTFont("Consolas-Bold", f"{FONTS}/consolab.ttf"))
registerFontFamily("Arial", normal="Arial", bold="Arial-Bold",
                   italic="Arial-Italic", boldItalic="Arial-Bold")
registerFontFamily("Consolas", normal="Consolas", bold="Consolas-Bold",
                   italic="Consolas", boldItalic="Consolas-Bold")

# ---------------------------------------------------------------- kolory
NAVY = colors.HexColor("#1f3a5f")
BLUE = colors.HexColor("#2d6cdf")
GRAY = colors.HexColor("#555555")
LIGHT = colors.HexColor("#f4f5f7")
BORDER = colors.HexColor("#cfd4da")
CODEBG = colors.HexColor("#f6f8fa")
CODEBORDER = colors.HexColor("#d0d7de")
CODEFG = colors.HexColor("#1a1a1a")
ACCENT = colors.HexColor("#0b7d4d")

# ---------------------------------------------------------------- style
S = {}
S["title"] = ParagraphStyle("title", fontName="Arial-Bold", fontSize=23, leading=28,
                            textColor=NAVY, alignment=TA_CENTER, spaceAfter=6)
S["subtitle"] = ParagraphStyle("subtitle", fontName="Arial", fontSize=12.5, leading=17,
                               textColor=GRAY, alignment=TA_CENTER, spaceAfter=4)
S["meta"] = ParagraphStyle("meta", fontName="Arial", fontSize=9.5, leading=13,
                           textColor=GRAY, alignment=TA_CENTER)
S["h1"] = ParagraphStyle("h1", fontName="Arial-Bold", fontSize=16, leading=20,
                         textColor=NAVY, spaceBefore=16, spaceAfter=4)
S["h2"] = ParagraphStyle("h2", fontName="Arial-Bold", fontSize=12.5, leading=16,
                         textColor=BLUE, spaceBefore=11, spaceAfter=3)
S["h3"] = ParagraphStyle("h3", fontName="Arial-Bold", fontSize=10.5, leading=14,
                         textColor=NAVY, spaceBefore=8, spaceAfter=2)
S["body"] = ParagraphStyle("body", fontName="Arial", fontSize=10, leading=14.5,
                           textColor=colors.black, alignment=TA_JUSTIFY, spaceAfter=6)
S["bullet"] = ParagraphStyle("bullet", fontName="Arial", fontSize=10, leading=14,
                             textColor=colors.black, leftIndent=14, bulletIndent=2,
                             spaceAfter=3)
S["code"] = ParagraphStyle("code", fontName="Consolas", fontSize=7.6, leading=10.2,
                           textColor=CODEFG, backColor=CODEBG, borderColor=CODEBORDER,
                           borderWidth=0.6, borderPadding=6, spaceBefore=4, spaceAfter=8,
                           leftIndent=0, firstLineIndent=0)
S["caption"] = ParagraphStyle("caption", fontName="Arial-Italic", fontSize=8.5,
                              leading=11, textColor=GRAY, spaceAfter=8)
S["cell"] = ParagraphStyle("cell", fontName="Arial", fontSize=9, leading=12)
S["cellb"] = ParagraphStyle("cellb", fontName="Arial-Bold", fontSize=9, leading=12)
S["cellc"] = ParagraphStyle("cellc", fontName="Consolas", fontSize=8.3, leading=11,
                            textColor=NAVY)
S["q"] = ParagraphStyle("q", fontName="Arial-Bold", fontSize=10, leading=13.5,
                        textColor=NAVY, spaceBefore=7, spaceAfter=2)
S["a"] = ParagraphStyle("a", fontName="Arial", fontSize=9.8, leading=13.5,
                        textColor=colors.black, spaceAfter=4, alignment=TA_JUSTIFY)

story = []


def P(text, style="body"):
    story.append(Paragraph(text, S[style]))


def H1(text):
    story.append(Spacer(1, 4))
    story.append(Paragraph(text, S["h1"]))
    story.append(HRFlowable(width="100%", thickness=1.1, color=NAVY,
                            spaceBefore=2, spaceAfter=6))


def H2(text):
    story.append(Paragraph(text, S["h2"]))


def H3(text):
    story.append(Paragraph(text, S["h3"]))


def UL(items):
    for it in items:
        story.append(Paragraph(it, S["bullet"], bulletText="•"))
    story.append(Spacer(1, 4))


def CODE(text, caption=None):
    # Preformatted renderuje tekst dosłownie (nie interpretuje encji XML),
    # więc NIE eskejpujemy — inaczej && pokazałoby się jako &amp;&amp;.
    story.append(Preformatted(text.rstrip("\n"), S["code"]))
    if caption:
        story.append(Paragraph(caption, S["caption"]))


def CAP(text):
    story.append(Paragraph(text, S["caption"]))


def SP(h=6):
    story.append(Spacer(1, h))


def read_file(path):
    with open(os.path.join(BASE, path), "r", encoding="utf-8") as f:
        return f.read()


def read_lines(path, start, end):
    with open(os.path.join(BASE, path), "r", encoding="utf-8") as f:
        lines = f.readlines()
    return "".join(lines[start - 1:end])


def table(data, col_widths, header=True):
    t = Table(data, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1),
         [colors.white, LIGHT]),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Arial-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
        ]
    t.setStyle(TableStyle(style))
    story.append(t)
    story.append(Spacer(1, 8))


def C(s):
    """inline code"""
    return f'<font name="Consolas" color="#0b3d91">{escape(s)}</font>'


# fragmenty z apostrofami/backslashami (poza f-stringami, by uniknąć błędu składni)
_bashc = C("bash -lc 'set -euo pipefail …'")
_printfc = C("printf '%s'")
_homec = C("\\$HOME")


# ================================================================ OKŁADKA
story.append(Spacer(1, 70))
P("Dokumentacja techniczna projektu", "title")
P("Discord Bot — TV, Giełda &amp; Asystent AI", "title")
SP(10)
P("Architektura aplikacji · biblioteki Python · Docker · CI/CD (GitHub Actions)", "subtitle")
P("Materiał przygotowawczy do rozmowy kwalifikacyjnej", "subtitle")
SP(30)
story.append(HRFlowable(width="55%", thickness=1, color=BORDER,
                        spaceBefore=2, spaceAfter=10, hAlign="CENTER"))
P("Repozytorium: <font name='Consolas'>tv_and_stockmarket_bot</font>", "meta")
P("Stack: Python 3.12 · discord.py · Flask · SQLite · Docker · GitHub Actions · Oracle Cloud", "meta")
story.append(PageBreak())

# ================================================================ 1. SYNOPSIS
H1("1. Synopsis — 30-sekundowa prezentacja projektu")
P("Poniższa wypowiedź to „elevator pitch” — warto znać jej kształt na pamięć. "
  "Zawiera język, framework, architekturę, sposób przechowywania danych, integracje "
  "zewnętrzne oraz pełną ścieżkę wdrożenia.")
story.append(Table(
    [[Paragraph(
        "„To wielofunkcyjny bot na Discorda napisany w Pythonie z użyciem "
        "<b>discord.py</b>. Jeden proces łączy kilkanaście obszarów funkcjonalnych: "
        "śledzenie premier seriali i filmów, notowania giełdowe i monitoring portfela, "
        "pogodę, przypomnienia, nawyki, listę książek i gier oraz asystenta AI opartego "
        "o Google Gemini. Pobiera dane z API (TMDB, Yahoo Finance, OpenWeatherMap, Gemini) "
        "i zapisuje wszystko w SQLite. Kod jest podzielony na <b>cogi</b> — po jednym na "
        "funkcję — oraz warstwową warstwę dostępu do danych, dzięki czemu każdy obszar "
        "jest odizolowany i testowalny. Aplikacja jest skonteneryzowana (Docker) i wdrażana "
        "przez pipeline CI/CD w GitHub Actions: każdy push do <b>main</b> uruchamia testy, "
        "buduje obraz, wypycha go do GitHub Container Registry, a następnie loguje się przez "
        "SSH na darmową maszynę Oracle Cloud i przewdraża kontener. Sekrety produkcyjne "
        "żyją w pliku <b>.env</b> na serwerze i nigdy nie trafiają do repozytorium.”",
        S["body"])]],
    colWidths=[170 * mm]))
story[-1].setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef4ff")),
    ("BOX", (0, 0), (-1, -1), 0.8, BLUE),
    ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ("TOPPADDING", (0, 0), (-1, -1), 8),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
]))
SP(10)

# ================================================================ 2. APLIKACJA
H1("2. Opis aplikacji — architektura")
P("Aplikacja to <b>bot na Discorda</b> — długo działający proces Pythona, który "
  "łączy się z bramą (gateway) Discorda po WebSocket i reaguje na dwa rodzaje komend:")
UL([
    f"<b>Komendy z prefiksem</b> ({C('!help')}, {C('!sync')}) — starszy styl.",
    f"<b>Komendy slash</b> ({C('/weather')}, {C('/ping')}) — nowoczesne „application commands” Discorda.",
    f"Wiele komend to <b>komendy hybrydowe</b> ({C('bot.hybrid_command')}) — ta sama funkcja "
    "dostępna jest na oba sposoby.",
])

H2("Trzy warstwy architektury")
H3("1. Punkt wejścia — bot.py")
UL([
    f"Najpierw konfiguruje logowanie ({C('logger.setup_logging()')}) — kolejność ma znaczenie, "
    "by nic nie logowało się bez formatowania.",
    f"Ładuje konfigurację, definiuje <b>intents</b> ({C('message_content = True')}) i tworzy "
    f"instancję {C('commands.Bot')}.",
    f"Ładuje ~13 <b>cogów</b> z listy {C('INITIAL_EXTENSIONS')}.",
    f"Posiada globalny <b>handler błędów</b> komend slash ({C('@bot.tree.error')}) — jedna "
    "wadliwa komenda nigdy nie wywraca bota.",
    f"W zdarzeniu {C('on_ready')} <b>synchronizuje drzewo komend</b> z Discordem (na poziomie "
    "serwera — natychmiast, oraz globalnie — do godziny propagacji).",
])
H3("2. Cogi — katalog cogs/ (moduły funkcji)")
P(f"Cogi to system wtyczek w discord.py. Każdy plik ({C('stocks.py')}, {C('tv_shows.py')}, "
  f"{C('gemini.py')}, {C('reminders.py')}, …) to samodzielna klasa grupująca powiązane "
  "komendy i pętle w tle. Dzięki temu duży bot pozostaje łatwy w utrzymaniu — każda "
  "funkcja jest odizolowana, można ją ładować/odładować niezależnie, a awaria jednego "
  "coga nie zatrzymuje pozostałych.")
H3("3. Warstwa danych — data_manager.py + data_manager_impl/")
P(f"{C('data_manager.py')} to cienka <b>fasada</b>: właściwa implementacja jest podzielona na "
  f"<b>miksiny</b> ({C('StocksMixin')}, {C('RemindersMixin')}, {C('MediaMixin')}, "
  f"{C('ProductivityMixin')} …), które przez wielodziedziczenie składają się w jedną klasę "
  f"{C('DataManager')}. Publiczne API pozostaje {C('from data_manager import DataManager')}, "
  "więc można refaktoryzować wnętrze bez dotykania kodu wywołującego.")
UL([
    f"Trwałość oparta o <b>SQLite</b> ({C('data/app.db')}). Plik {C('core.py')} trzyma "
    f"połączenie, wątkowo-bezpieczny {C('_execute_query')} (chroniony przez {C('RLock')}, bo "
    f"połączenie jest współdzielone między wątkami przy {C('check_same_thread=False')}) oraz "
    f"{C('_initialize_db()')}, które tworzy ~20 tabel ({C('CREATE TABLE IF NOT EXISTS')}).",
    f"Warstwa wykonuje też <b>lekkie migracje schematu</b> — sprawdza {C('PRAGMA table_info')} "
    f"i uruchamia {C('ALTER TABLE ADD COLUMN')} dla starszych baz, by zaktualizować działającą "
    "bazę bez utraty danych użytkowników.",
])

H2("Dwa wbudowane serwery WWW")
P("Bot to nie tylko klient Discorda — uruchamia też <b>serwer Flask w osobnym wątku</b>:")
UL([
    f"{C('GET /')} — endpoint uptime/health („Bot is alive and kicking!”).",
    f"{C('POST /webhook/report/<token>')} — webhook przychodzący, który pozwala zewnętrznej "
    "usłudze wepchnąć raport, a bot przekazuje go w DM do użytkownika. Endpoint jest "
    "zabezpieczony: weryfikacja podpisu <b>HMAC-SHA256</b>, <b>rate limiter</b> w oknie "
    "przesuwnym, limit rozmiaru żądania i mapowanie token→użytkownik.",
])
P(f"Most między wątkami realizuje {C('asyncio.run_coroutine_threadsafe')}, bo Flask działa "
  "na innym wątku niż pętla zdarzeń asyncio bota.")
P(f"Osobny plik {C('stock_proxy_service.py')} to mała usługa Flask uruchamiana na "
  f"<b>hoście</b>, która pobiera dane z Yahoo Finance i udostępnia je kontenerowi na porcie "
  f"9999 (przez {C('host.docker.internal')}). Powstała, bo sieciowanie Dockera przeszkadzało "
  "w żądaniach do Yahoo Finance — to dobra „historia z życia” na rozmowę.")

H2("Konfiguracja — config.py")
P(f"Używa <b>Pydantic Settings</b> ({C('BaseSettings')}) do wczytania i <b>walidacji</b> "
  f"zmiennych środowiskowych z {C('.env')}. Pydantic daje typowaną, walidowaną konfigurację "
  f"— jeśli brakuje wymaganego sekretu jak {C('DISCORD_BOT_TOKEN')}, aplikacja zatrzymuje "
  "się od razu na starcie z czytelną informacją, zamiast paść później w niejasny sposób.")

H2("Integracje zewnętrzne — api_clients/")
P("Każda usługa ma osobny moduł klienta: TMDB i TVMaze (seriale/filmy), Yahoo Finance i "
  "Alpha Vantage (giełda), OpenWeatherMap (pogoda), OpenLibrary (książki), Steam / "
  "PCGamingWiki / Wikipedia (gry), Google News RSS. Czysty podział: cogi obsługują logikę "
  "Discorda, klienci obsługują logikę HTTP/API.")

H2("Testy — tests/")
P(f"{C('pytest')} z {C('pytest-asyncio')}. Plik {C('conftest.py')} ustawia atrapy zmiennych "
  f"środowiskowych (by walidacja config przeszła w CI) i dostarcza fixture {C('db_manager')}, "
  "który tworzy <b>DataManager na tymczasowej bazie SQLite</b> dla każdego testu — warstwa "
  "danych jest testowana naprawdę, w izolacji, bez zależności zewnętrznych.")

story.append(PageBreak())

# ================================================================ 3. BIBLIOTEKI
H1("3. Biblioteki Python")
P(f"Zależności aplikacji deklaruje plik {C('requirements.txt')} (instalowany w obrazie "
  "Dockera). Poniżej jego pełna treść, a dalej omówienie roli każdej biblioteki.")
CODE(read_file("requirements.txt"), "requirements.txt — pełna treść")

libs = [
    ("discord.py", "Framework bota Discord: połączenie z gateway (WebSocket), system komend, "
                    "cogi, komendy slash/hybrydowe, obsługa zdarzeń i interakcji."),
    ("python-dotenv", "Wczytywanie zmiennych środowiskowych z pliku .env do procesu "
                       "(uzupełnia Pydantic Settings podczas lokalnego developmentu)."),
    ("requests", "Synchroniczny klient HTTP do wywołań zewnętrznych API "
                 "(TMDB, OpenWeatherMap, Steam, Wikipedia itp.). Przypięta wersja >=2.28.0."),
    ("Flask", "Lekki serwer WWW. Hostuje endpoint health/uptime, webhook raportów oraz "
              "osobną usługę stock-proxy."),
    ("yfinance", "Pobieranie danych giełdowych z Yahoo Finance (notowania, szeregi czasowe). "
                 "Wersja >=0.2.18."),
    ("urllib3", "Niskopoziomowa warstwa HTTP (zależność requests); przypięta >=1.26.0 dla "
                "zgodności/bezpieczeństwa."),
    ("google-genai", "Oficjalne SDK Google Gemini — zasila coga asystenta AI."),
    ("beautifulsoup4", "Parser HTML do scrapowania/wyciągania danych ze stron "
                       "(np. PCGamingWiki, Wikipedia) tam, gdzie nie ma czystego API."),
    ("pydantic", "Walidacja danych i modele typowane — baza dla pydantic-settings."),
    ("pydantic-settings", "Typowana i walidowana konfiguracja z .env / zmiennych "
                          "środowiskowych (klasa Settings w config.py)."),
]
table(
    [[Paragraph("Biblioteka", S["cellb"]), Paragraph("Rola w projekcie", S["cellb"])]]
    + [[Paragraph(n, S["cellc"]), Paragraph(d, S["cell"])] for n, d in libs],
    [38 * mm, 132 * mm])
P("W CI doinstalowywane są dodatkowo biblioteki testowe: <font name='Consolas' "
  "color='#0b3d91'>pytest</font> oraz <font name='Consolas' color='#0b3d91'>pytest-asyncio</font> "
  "(tryb asyncio w pytest.ini ustawiony na <font name='Consolas' color='#0b3d91'>auto</font>).")

story.append(PageBreak())

# ================================================================ 4. DOCKERFILE
H1("4. Dockerfile — analiza szczegółowa")
P(f"Plik {C('bot.Dockerfile')} buduje obraz aplikacji. To build <b>jednoetapowy</b> "
  "(single-stage). Poniżej pełna, niezmieniona zawartość pliku, a następnie omówienie "
  "każdego fragmentu wraz z uzasadnieniem decyzji.")
CODE(read_file("bot.Dockerfile"), "bot.Dockerfile — pełna treść")

H2("Analiza linia po linii")

H3("Obraz bazowy")
CODE("FROM python:3.12-slim")
UL([
    "Oficjalny obraz Pythona 3.12, wariant <b>-slim</b>.",
    "<b>slim</b> utrzymuje mały rozmiar obrazu (bez kompilatorów/dokumentacji), ale wciąż "
    "bazuje na Debianie, więc można doinstalować pakiety przez <font name='Consolas'>apt-get</font>.",
    "Dlaczego nie Alpine? Alpine używa musl libc, co często psuje gotowe „wheels” Pythona "
    "i wymusza wolną kompilację ze źródeł. Dla Pythona <b>slim</b> to bezpieczniejszy domyślny wybór.",
])

H3("Pakiety systemowe")
CODE("RUN apt-get update && apt-get install -y \\\n"
     "    curl ca-certificates dnsutils iputils-ping build-essential \\\n"
     "    && rm -rf /var/lib/apt/lists/*")
UL([
    f"{C('curl')}, {C('dnsutils')}, {C('iputils-ping')} — narzędzia do <b>debugowania sieci</b> "
    "(ten bot miał realne problemy z DNS/siecią).",
    f"{C('ca-certificates')} — certyfikaty do połączeń HTTPS.",
    f"{C('build-essential')} — kompilatory, by pip mógł <b>zbudować pakiet</b>, który nie ma "
    "gotowego wheela.",
    f"<b>Kluczowy detal:</b> {C('rm -rf /var/lib/apt/lists/*')} w <u>tym samym</u> {C('RUN')} "
    "usuwa cache apt w tej samej warstwie, by nie powiększał obrazu. Gdyby sprzątanie było w "
    "osobnym RUN, cache zostałby w warstwie wcześniejszej.",
])

H3("Warstwa zależności — najważniejsza koncepcja")
CODE("WORKDIR /app\n"
     "COPY requirements.txt .\n"
     "RUN pip install --upgrade pip && \\\n"
     "    pip install --no-cache-dir -r requirements.txt")
UL([
    "<b>Kopiujemy requirements.txt PRZED kodem aplikacji.</b> To najważniejsza koncepcja "
    "Dockera do omówienia: <b>cache warstw</b>.",
    f"Docker cache'uje każdą warstwę. Dopóki {C('requirements.txt')} się nie zmienia, "
    "kosztowna warstwa <font name='Consolas'>pip install</font> jest używana ponownie nawet, "
    f"gdy zmienisz tylko {C('bot.py')}.",
    "Gdyby najpierw skopiować cały kod, każda zmiana w kodzie unieważniałaby cache zależności.",
    f"{C('--no-cache-dir')} trzyma cache pobierania pip poza obrazem (mniejszy obraz).",
])

H3("Kopiowanie kodu aplikacji")
CODE(read_lines("bot.Dockerfile", 27, 34))
UL([
    f"Kod kopiowany jest jawnie (zamiast {C('COPY . .')}), co utrzymuje śmieci poza obrazem.",
    "Te warstwy zmieniają się często, dlatego są na końcu — by nie unieważniać warstwy "
    "zależności powyżej.",
])

H3("Port i komenda startowa")
CODE("EXPOSE 5000\nCMD [\"python\", \"bot.py\"]")
UL([
    f"{C('EXPOSE 5000')} dokumentuje port Flaska — to metadana, sam w sobie nie publikuje "
    f"portu; robi to {C('-p 5000:5000')} przy {C('docker run')}.",
    f"{C('CMD')} w <b>formie exec</b> (tablica JSON), a nie formie shell. Forma exec sprawia, "
    "że proces jest bezpośrednio PID 1 i poprawnie odbiera sygnały (np. SIGTERM) do czystego "
    "zamknięcia.",
])

H2("Częste pytania o Dockerfile")
P(f"<b>Jak zmniejszyć / zabezpieczyć obraz?</b> Build wieloetapowy (kompilacja zależności w "
  f"etapie buildera, kopiowanie tylko zainstalowanych pakietów do czystego etapu runtime) oraz "
  f"dodanie użytkownika nie-root ({C('USER')}). Dobry punkt jako „co bym poprawił”.")
P("<b>Dlaczego nie Alpine?</b> musl libc często łamie wheels Pythona i wymusza wolne "
  "kompilacje — slim jest bezpieczniejszy dla Pythona.")

story.append(PageBreak())

# ================================================================ 5. WORKFLOW
H1("5. GitHub Actions — pipeline CI/CD")
P(f"Plik {C('.github/workflows/deploy.yml')} definiuje pełny pipeline. Poniżej jego "
  "kompletna treść, a następnie analiza wyzwalacza i trzech zadań (jobs).")
CODE(read_file(".github/workflows/deploy.yml"), ".github/workflows/deploy.yml — pełna treść")

H2("Wyzwalacz")
CODE(read_lines(".github/workflows/deploy.yml", 3, 6))
P(f"Uruchamia się przy każdym pushu do gałęzi {C('main')}, a {C('workflow_dispatch')} "
  "pozwala uruchomić go ręcznie z poziomu interfejsu GitHub.")
P(f"Pipeline składa się z <b>trzech sekwencyjnych zadań</b> połączonych {C('needs:')} — "
  "to właśnie bramkowanie (gating) jest najważniejsze.")

H2("Job 1 — test")
CODE(read_lines(".github/workflows/deploy.yml", 13, 39))
UL([
    "Checkout kodu, ustawienie Pythona 3.12 (z cache pip między uruchomieniami).",
    f"Instalacja {C('requirements.txt')} + {C('pytest')} i {C('pytest-asyncio')}.",
    f"Ustawienie {C('PYTHONPATH')} na katalog główny repo, by testy widziały moduły.",
    f"Uruchomienie {C('pytest -vv tests/')}.",
    "<b>To bramka jakości:</b> jeśli testy padną, pipeline zatrzymuje się tu i nic nie "
    "zostaje zbudowane ani wdrożone.",
])

H2("Job 2 — build-and-push (needs: test)")
P("Uruchamia się tylko, gdy testy przeszły.")
CODE(read_lines(".github/workflows/deploy.yml", 41, 93))
UL([
    f"{C('permissions: packages: write')} — nadaje wbudowanemu {C('GITHUB_TOKEN')} prawo "
    "wypychania do GHCR.",
    f"<b>Zmiana nazwy repo na małe litery:</b> {C('REPO_LC=${GITHUB_REPOSITORY,,}')} — "
    "rejestry Dockera wymagają małych liter, a nazwy repo na GitHubie mogą mieć wielkie.",
    f"Logowanie do <b>GHCR</b> ({C('ghcr.io')}) przez {C('github.actor')} i wbudowany "
    f"{C('secrets.GITHUB_TOKEN')} — bez ręcznie zarządzanego hasła do rejestru.",
    f"<b>Buildx + metadata-action</b> generują tagi: {C('latest')} oraz tag oparty o "
    f"{C('sha')} commita (każdy obraz jest identyfikowalny do commita).",
    f"Build z {C('bot.Dockerfile')}, push, oraz <b>cache GitHub Actions</b> "
    f"({C('cache-from/to: type=gha')}) przyspieszający budowanie warstw między uruchomieniami.",
])

H2("Job 3 — deploy (needs: build-and-push)")
P("To część robiąca wrażenie — pokazuje dyscyplinę w obsłudze sekretów i unika kruchych "
  "akcji firm trzecich na rzecz czystego SSH.")
CODE(read_lines(".github/workflows/deploy.yml", 95, 145))
UL([
    f"{C('webfactory/ssh-agent')} ładuje <b>klucz prywatny SSH</b> ({C('secrets.OCI_SSH_KEY')}) "
    "do agenta.",
    f"{C('ssh-keyscan')} dodaje serwer do {C('known_hosts')}, by SSH nie pytało o "
    "autentyczność hosta.",
    f"<b>Budowanie pliku .env z sekretów:</b> {C('printf')} zapisuje zbiorczy sekret "
    f"{C('ENV_FILE')}, a następnie {C('awk')} <b>usuwa zduplikowane klucze</b> przed ponownym "
    f"dopisaniem konkretnych sekretów Firebase/timer. Powod: {C('docker --env-file')} przy "
    "duplikatach stosuje regułę „ostatni wygrywa”, co łatwo źle odczytać podczas debugowania.",
    f"{C('scp')} kopiuje {C('.env')} na serwer.",
])
P("Ostatni krok — właściwe przewdrożenie kontenera na serwerze:")
CODE(read_lines(".github/workflows/deploy.yml", 147, 171))
UL([
    f"Token rejestru jest podawany przez <b>stdin</b> ({C('printf ... | ssh ...')}), więc nigdy "
    "nie ląduje w zmiennej środowiskowej ani w historii powłoki.",
    _bashc + " — tryb ścisły: skrypt pada głośno przy każdym błędzie.",
    f"{C('docker stop bot-container || true')} — {C('|| true')} oznacza „nie przerywaj, jeśli "
    "kontener jeszcze nie istnieje”.",
    f"<b>{C('-v $HOME/data:/app/data')}</b> — bind-mount (wolumen), dzięki któremu baza SQLite "
    "<b>przeżywa przewdrożenie</b>. Bez tego każdy deploy kasowałby dane użytkowników — to "
    "najważniejszy detal produkcyjny.",
    f"<b>{C('--restart unless-stopped')}</b> — kontener wraca po restarcie/awarii serwera.",
    f"{C('docker image prune -f')} — sprząta stare „dangling” obrazy (odzyskuje miejsce).",
    "<b>Ekspansja zmiennych lokalnie vs zdalnie:</b> " + C('$REGISTRY') + "/" + C('$IMAGE')
    + " rozwijają się na runnerze; " + _homec + " jest eskejpowane, by rozwinąć się na "
    "serwerze. Wiedza, po której stronie rozwija się zmienna, to ostry detal.",
])

H2("Sekrety użyte w pipeline")
secrets = [
    ("GITHUB_TOKEN", "Wbudowany token — logowanie i push do GHCR (Job 2)."),
    ("OCI_SSH_KEY", "Klucz prywatny SSH do maszyny Oracle Cloud."),
    ("OCI_HOST / OCI_USERNAME", "Adres i użytkownik serwera docelowego."),
    ("ENV_FILE", "Zbiorcza zawartość pliku .env (sekrety runtime)."),
    ("FIREBASE_DATABASE_URL / _SECRET", "Dostęp do Firebase dla funkcji timer (owner-only)."),
    ("TIMER_OWNER_ID, DISCORD_SYNC_SECRET, TIMER_AUTH_PASSWORD",
     "Dodatkowe sekrety funkcji timer wstrzykiwane bez duplikatów."),
]
table(
    [[Paragraph("Sekret", S["cellb"]), Paragraph("Zastosowanie", S["cellb"])]]
    + [[Paragraph(n, S["cellc"]), Paragraph(d, S["cell"])] for n, d in secrets],
    [62 * mm, 108 * mm])

P("<b>Uwaga o niezgodności dokumentacji:</b> plik <font name='Consolas'>PIPELINE.md</font> "
  "opisuje deploy przez <font name='Consolas'>docker compose up -d</font>, podczas gdy "
  "rzeczywisty <font name='Consolas'>deploy.yml</font> używa surowego <font name='Consolas'>"
  "docker run</font>. Jeśli padnie pytanie: dokumentacja opisuje wcześniejsze podejście "
  "oparte o compose; workflow obecnie robi przewdrożenie bezpośrednio przez "
  "<font name='Consolas'>docker run</font>.")

story.append(PageBreak())

# ================================================================ 6. COMPOSE
H1("6. docker-compose.yml i Procfile")
P(f"{C('docker-compose.yml')} ułatwia lokalne uruchomienie (build + run jedną komendą) i "
  "definiuje politykę restartu oraz mapowanie portu.")
CODE(read_file("docker-compose.yml"), "docker-compose.yml")
UL([
    f"{C('build.dockerfile: bot.Dockerfile')} — wskazuje niestandardową nazwę Dockerfile.",
    f"{C('restart: unless-stopped')} — ta sama polityka co na produkcji.",
    f"{C('ports: 5000:5000')} — wystawia serwer Flask na hosta.",
])
P(f"{C('Procfile')} (format Heroku) deklaruje proces typu web:")
CODE(read_file("Procfile"), "Procfile")
P("Pozostałość po platformie PaaS — mówi „uruchom proces web jako "
  "<font name='Consolas'>python bot.py</font>”. Na obecnym wdrożeniu (Docker na Oracle "
  "Cloud) nie jest używany, ale warto wiedzieć, co to jest.")

# ================================================================ 7. KOMENDY LINUX
H1("7. Komendy Linux / Docker do opanowania")
P("Skoro wdrożenie idzie na maszynę Linux i budujesz obrazy Linuksowe, spodziewaj się "
  "pytań o linię komend. Pogrupowane tematycznie.")

H2("Docker")
docker_cmds = [
    ("docker build -f bot.Dockerfile -t discord-bot:latest .", "Budowa obrazu z nazwanego Dockerfile."),
    ("docker run -d --name bot-container --restart unless-stopped\n  -p 5000:5000 -v $HOME/data:/app/data --env-file .env <img>",
     "Uruchomienie w tle: polityka restartu, mapowanie portu, wolumen, plik env."),
    ("docker ps  /  docker ps -a", "Lista działających / wszystkich kontenerów."),
    ("docker logs -f bot-container", "Śledzenie logów kontenera (debug na produkcji)."),
    ("docker exec -it bot-container bash", "Wejście do powłoki wewnątrz działającego kontenera."),
    ("docker pull / stop / rm", "Pobranie, zatrzymanie, usunięcie kontenera."),
    ("docker image prune -f", "Usunięcie „dangling” obrazów (odzysk miejsca)."),
    ("docker compose up -d  /  down", "Podniesienie / zatrzymanie stacku compose w tle."),
]
table(
    [[Paragraph("Komenda", S["cellb"]), Paragraph("Znaczenie", S["cellb"])]]
    + [[Preformatted(c, S["cellc"]), Paragraph(d, S["cell"])] for c, d in docker_cmds],
    [86 * mm, 84 * mm])

H2("SSH i transfer plików (używane w deployu)")
UL([
    f"{C('ssh user@host')}, {C('ssh-keygen')}, {C('ssh-keyscan -H host >> ~/.ssh/known_hosts')}",
    f"{C('scp plik user@host:~/')} — kopiowanie pliku na serwer.",
])

H2("Procesy i system (debug zawieszonego bota)")
UL([
    f"{C('ps aux | grep python')}, {C('top')} / {C('htop')}, {C('kill -9 <pid>')}",
    f"{C('df -h')} (dysk — obrazy/logi go zapełniają), {C('free -h')} (pamięć)",
    f"{C('systemctl status docker')}, {C('journalctl -u docker')}",
])

H2("Tekst i pliki (używane w samym workflow)")
UL([
    f"{C('grep')} (szukanie), {C('awk')} (przetwarzanie pól/linii — deduplikacja kluczy env), "
    f"{C('sed')} (edycja strumieniowa)",
    f"{C('cat')}, {C('less')}, {C('tail -f')} (śledzenie logu), {C('chmod')} / {C('chown')} "
    f"(uprawnienia — np. {C('chmod 600')} na kluczu SSH)",
    _printfc + " vs " + C('echo') + " — printf zachowuje treść dokładnie (użyte do "
    "zapisu tokenu/.env bez zbędnych znaków nowej linii).",
])

H2("Koncepcje skryptowania powłoki w workflow")
UL([
    f"{C('set -euo pipefail')} — przerwij przy błędzie / niezdefiniowanej zmiennej / błędzie potoku.",
    f"{C('cmd || true')} — połknij niezerowy kod wyjścia, by skrypt szedł dalej.",
    f"{C('>>')} (dopisz) vs {C('>')} (nadpisz); potok {C('|')}; {C('--password-stdin')} by "
    "trzymać sekrety poza linią komend.",
])

story.append(PageBreak())

# ================================================================ 8. PYTANIA
H1("8. Pytania rekrutacyjne — błyskawiczne odpowiedzi")
qa = [
    ("Dlaczego jeden bot z cogami zamiast mikroserwisów?",
     "Jeden artefakt do wdrożenia, wspólne połączenie do DB i jedna pętla zdarzeń, niski "
     "koszt operacyjny na darmowej maszynie. Cogi i tak dają separację logiczną. Mikroserwisy "
     "dorzuciłyby koszt sieci/ops bez korzyści przy tej skali."),
    ("Jak chronisz sekrety?",
     "Nigdy nie commitowane (.env w .gitignore); sekrety CI w GitHub Secrets; sekrety runtime "
     "kopiowane przez scp na serwer; token rejestru podawany przez stdin; Pydantic waliduje je "
     "na starcie."),
    ("Jak dane przeżywają deploy?",
     "SQLite na bind-mount wolumenie (-v $HOME/data:/app/data). Kontener jest jednorazowy, "
     "dane już nie."),
    ("Co się dzieje, gdy test padnie?",
     "Łańcuch needs: zatrzymuje pipeline na zadaniu test — brak builda, brak deployu."),
    ("Dlaczego Flask wewnątrz bota Discord?",
     "Endpoint health/uptime + odbieranie webhooków. Działa w wątku-demonie obok pętli "
     "asyncio, most przez run_coroutine_threadsafe."),
    ("Największe wyzwanie?",
     "Sieciowanie Dockera psuło wywołania do Yahoo Finance — rozwiązane usługą stock-proxy "
     "po stronie hosta; oraz niezawodne pojawianie się komend slash w DM (logika "
     "_enable_dm_for_app_commands)."),
    ("Co byś poprawił?",
     "Build wieloetapowy + użytkownik nie-root; logowanie strukturalne/metryki; migracja z "
     "SQLite na Postgres przy większej współbieżności/skali; deploy bramkowany health-checkiem "
     "(weryfikacja nowego kontenera przed prune)."),
]
for q, a in qa:
    story.append(KeepTogether([Paragraph("Q: " + q, S["q"]), Paragraph("A: " + a, S["a"])]))

story.append(PageBreak())

# ================================================================ 9. STRESZCZENIE
H1("9. Streszczenie — wszystko w pigułce")
P("Skondensowany przegląd do szybkiej powtórki przed rozmową. Po jednym zdaniu na obszar.")
tldr = [
    ("Aplikacja", "Bot na Discorda (Python, discord.py). Jeden proces, ~13 modułów-cogów "
                  "(TV/filmy, giełda, pogoda, przypomnienia, nawyki, książki, gry, AI Gemini). "
                  "Dane w SQLite przez DataManager złożony z miksinów. Dodatkowo serwer Flask w "
                  "wątku: health + webhook. Konfiguracja walidowana Pydantic."),
    ("Biblioteki", "discord.py (framework), Flask (health/webhook), requests + yfinance (HTTP + "
                   "giełda), google-genai (AI), beautifulsoup4 (scraping), pydantic + "
                   "pydantic-settings (typowana config), python-dotenv. Testy: pytest + "
                   "pytest-asyncio."),
    ("Dockerfile", "Baza python:3.12-slim; narzędzia sieciowe + build-essential; kopiowanie "
                   "requirements.txt PRZED kodem (cache warstw); pip install; kopia kodu; "
                   "EXPOSE 5000; CMD w formie exec dla poprawnej obsługi sygnałów."),
    ("Workflow (CI/CD)", "Push do main → (1) test: pytest jako bramka jakości; (2) build-and-push: "
                         "build obrazu i push do GHCR z tagami latest + sha; (3) deploy: SSH na VM "
                         "Oracle Cloud, zapis .env z sekretów, docker pull + odtworzenie kontenera "
                         "z wolumenem danych i restart unless-stopped. Joby spięte needs:."),
    ("Linux/Docker", "docker build/run/logs/exec/ps (kontenery), ssh/scp/ssh-keyscan (zdalny "
                     "deploy), awk/grep/printf (przetwarzanie tekstu w workflow), "
                     "set -euo pipefail (głośne błędy), || true (ignoruj oczekiwane błędy), "
                     "-v host:kontener (trwały wolumen)."),
]
table(
    [[Paragraph("Obszar", S["cellb"]), Paragraph("W skrócie", S["cellb"])]]
    + [[Paragraph(n, S["cellb"]), Paragraph(d, S["cell"])] for n, d in tldr],
    [34 * mm, 136 * mm])

# ================================================================ 10. PROBLEMY
H1("10. Problemy napotkane w projekcie")
P("Te problemy są <b>udokumentowane w kodzie i historii commitów</b> — to wiarygodne, szczere "
  "odpowiedzi na pytanie „z czym było najtrudniej”. Każdy ma ślad w repozytorium.")
problems = [
    ("Docker nie mógł połączyć się z Yahoo Finance",
     "komentarz w nagłówku stock_proxy_service.py",
     "Proxy Flask po stronie hosta; kontener woła je przez host.docker.internal."),
    ("Błędy rozwiązywania DNS w kontenerze",
     "Dockerfile instaluje dnsutils/iputils-ping/curl; opcja BOT_DNS w skrypcie PS1",
     "Narzędzia do debugowania + opcjonalna flaga --dns."),
    ("Komendy slash nie pojawiały się w DM",
     "_enable_dm_for_app_commands w bot.py + commit „restore DM slash visibility”",
     "Jawne ustawienie allowed_contexts + allowed_installs na każdej komendzie/grupie."),
    ("Opóźnienia/synchronizacja komend slash",
     "logika sync w on_ready + komenda !sync",
     "Kopia komend na poziom serwera (natychmiast) + ręczny !sync dla nowych serwerów."),
    ("Zduplikowane klucze w .env przy deployu",
     "blok awk w deploy.yml + commit „dedupe Firebase/timer env keys”",
     "Usunięcie wstrzykiwanych kluczy przed ponownym dopisaniem (reguła „ostatni wygrywa”)."),
    ("Zmiany schematu działającej bazy bez utraty danych",
     "PRAGMA table_info + ALTER TABLE w core.py",
     "Addytywne migracje uruchamiane przy starcie."),
    ("Wątek Flask vs pętla asyncio",
     "RLock + asyncio.run_coroutine_threadsafe w warstwie danych",
     "Bezpieczny most między wątkami; serializacja współdzielonego połączenia SQLite."),
    ("Ryzyko wycieku tokenu rejestru w deployu",
     "printf ... | ssh z --password-stdin",
     "Sekret podawany przez stdin — nigdy w zmiennej środowiskowej ani historii powłoki."),
]
table(
    [[Paragraph("Problem", S["cellb"]), Paragraph("Ślad w repo", S["cellb"]),
      Paragraph("Rozwiązanie", S["cellb"])]]
    + [[Paragraph(p, S["cell"]), Paragraph(e, S["cell"]), Paragraph(r, S["cell"])]
       for p, e, r in problems],
    [46 * mm, 56 * mm, 68 * mm])

# ================================================================ 11. USPRAWNIENIA
H1("11. Sugestie usprawnień")
P("Twoja odpowiedź na „co byś zrobił dalej / co poprawił”. Pogrupowane tematycznie.")

H2("Docker")
UL([
    "Build <b>wieloetapowy</b> (builder + slim runtime) → mniejszy obraz, mniejsza powierzchnia ataku.",
    f"Uruchamianie jako <b>użytkownik nie-root</b> ({C('USER')}); dodanie {C('HEALTHCHECK')}.",
    f"Przypięcie dokładnych wersji / plik lock ({C('pip-tools')} / {C('uv')}) → powtarzalne buildy.",
])
H2("Runtime / dane")
UL([
    f"Zamiana serwera deweloperskiego Flask na <b>gunicorn/waitress</b> (dev server nie jest produkcyjny).",
    f"Przejście z surowego {C('ALTER TABLE')} na prawdziwe <b>migracje (Alembic)</b>; "
    "rozważenie <b>Postgres</b> przy większej współbieżności; "
    f"użycie {C('aiosqlite')}, by zapytania do bazy nie blokowały pętli zdarzeń.",
])
H2("CI/CD")
UL([
    f"Dodanie <b>lintu + kontroli typów</b> ({C('ruff')}, {C('mypy')}) oraz wpięcie skanu "
    f"<b>bandit</b> (masz już {C('bandit_report.json')}) do pipeline'u.",
    f"<b>Deploy bramkowany health-checkiem</b> z rollbackiem: weryfikacja zdrowia nowego "
    f"kontenera <u>przed</u> {C('docker image prune')}.",
    f"<b>Menedżer sekretów</b> (lub przynajmniej {C('chmod 600')} i ograniczony zakres) zamiast "
    f"jawnego {C('.env')} na maszynie.",
])
H2("Obserwowalność")
UL([
    "Logowanie strukturalne + rotacja logów + zapis logów na wolumen "
    "(obecnie logi trafiają tylko na stdout).",
])

SP(8)
story.append(HRFlowable(width="100%", thickness=0.8, color=BORDER, spaceAfter=6))
P("Dokument wygenerowany automatycznie na podstawie kodu źródłowego repozytorium "
  "<font name='Consolas'>tv_and_stockmarket_bot</font>. Kod w listingach jest wierny 1:1 "
  "z plikami w repo.", "caption")


# ---------------------------------------------------------------- numeracja stron
def on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont("Arial", 8)
    canvas.setFillColor(GRAY)
    canvas.drawString(20 * mm, 12 * mm, "Discord Bot — dokumentacja projektu")
    canvas.drawRightString(190 * mm, 12 * mm, f"Strona {doc.page}")
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(20 * mm, 15 * mm, 190 * mm, 15 * mm)
    canvas.restoreState()


doc = BaseDocTemplate(
    OUT, pagesize=A4,
    leftMargin=20 * mm, rightMargin=20 * mm,
    topMargin=18 * mm, bottomMargin=20 * mm,
    title="Dokumentacja projektu — Discord Bot",
    author="kamil",
)
frame = Frame(doc.leftMargin, doc.bottomMargin,
              doc.width, doc.height, id="main")
doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=on_page)])
doc.build(story)
print("OK ->", OUT)
