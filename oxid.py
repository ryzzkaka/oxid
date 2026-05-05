import discord
import requests
from discord.ext import commands
from discord.ui import View
import os
import asyncio
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()  # lädt .env datei

# ─── Konstanten ───────────────────────────────────────────────
BRAND_COLOR  = 0x5865F2  # discord blurple
SUCCESS_COLOR = 0x57F287  # grün
ERROR_COLOR   = 0xED4245  # rot
STAFF_ID      = 1433047901407416372  # staff user id
FOOTER_TEXT   = "Shop Support"  # footer text für alle embeds

# ─── Supabase & Bot Setup ─────────────────────────────────────
token    = os.environ['oxid']          # discord bot token
api      = os.environ['sellauth']      # sellauth api key
shop     = os.environ['shop']          # sellauth shop id
url      = os.environ['supabase_url']  # supabase projekt url
key      = os.environ['supabase_key']  # supabase api key

supabase = create_client(url, key)  # supabase client erstellen

intents = discord.Intents.default()  # standard discord permissions
intents.message_content = True       # erlaubt nachrichten zu lesen
intents.dm_messages     = True       # erlaubt DMs zu empfangen

bot = commands.Bot(command_prefix="!", intents=intents)  # bot instanz


# ─── Hilfsfunktionen ──────────────────────────────────────────
def make_embed(title: str, description: str = None, color: int = BRAND_COLOR) -> discord.Embed:
    """erstellt ein embed mit einheitlichem footer und timestamp"""
    embed = discord.Embed(title=title, description=description, color=color)  # embed objekt
    embed.set_footer(text=FOOTER_TEXT)        # footer setzen
    embed.timestamp = discord.utils.utcnow()  # aktuellen timestamp setzen
    return embed  # fertiges embed zurückgeben


def check_invoice(invoice_id: str) -> dict:
    """ruft invoice daten von sellauth api ab"""
    r = requests.get(
        f"https://api.sellauth.com/v1/shops/{shop}/invoices/{invoice_id}",  # sellauth endpoint
        headers={"Authorization": f"Bearer {api}"}  # auth header
    )
    r.raise_for_status()  # wirft error wenn status nicht 200
    return r.json()        # gibt antwort als dict zurück


def check_supabase(name: str, invoice_id: str):
    """prüft ob produkt verfügbar und invoice nicht bereits geclaimed"""
    claimed = supabase.table("claimed_invoices").select("*").eq("invoice", invoice_id).execute()  # invoice in claimed tabelle suchen
    if len(claimed.data) > 0:  # invoice schon geclaimed
        return True

    data = supabase.table("products").select("*").eq("product", name).eq("claimed", False).execute()  # verfügbares produkt suchen
    if len(data.data) == 0:  # kein produkt gefunden
        return None

    return data.data[0]  # erstes verfügbares produkt zurückgeben


# ─── Views (Buttons) ──────────────────────────────────────────
class TicketButtons(View):
    """buttons für ticket-typ auswahl"""
    def __init__(self):
        super().__init__(timeout=None)  # kein timeout
        self.result = None              # speichert welchen button gedrückt wurde

    @discord.ui.button(label="Staff", style=discord.ButtonStyle.blurple)
    async def staff_button(self, interaction: discord.Interaction, button):
        self.result = "staff"          # ergebnis setzen
        await interaction.response.defer()  # interaction bestätigen
        self.stop()                    # view stoppen

    @discord.ui.button(label="Claim Purchase", style=discord.ButtonStyle.green)
    async def claim_button(self, interaction: discord.Interaction, button):
        self.result = "claim"          # ergebnis setzen
        await interaction.response.defer()  # interaction bestätigen
        self.stop()                    # view stoppen


class ConfirmView(View):
    """ja/nein buttons zur bestätigung"""
    def __init__(self):
        super().__init__(timeout=60)  # 60 sekunden timeout
        self.result = None            # speichert antwort

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green)
    async def yes(self, interaction: discord.Interaction, button):
        self.result = "yes"                # ergebnis setzen
        await interaction.response.defer()  # interaction bestätigen
        self.stop()                        # view stoppen

    @discord.ui.button(label="No", style=discord.ButtonStyle.red)
    async def no(self, interaction: discord.Interaction, button):
        self.result = "no"                 # ergebnis setzen
        await interaction.response.defer()  # interaction bestätigen
        self.stop()                        # view stoppen


# ─── Events ───────────────────────────────────────────────────
@bot.event
async def on_guild_channel_create(channel):
    """feuert wenn ein neuer channel erstellt wird"""
    if "ticket" not in channel.name.lower():  # nur ticket channels verarbeiten
        return

    await asyncio.sleep(1)  # kurz warten damit channel bereit ist

    # ── Schritt 1: Ticket-Typ wählen ──
    embed = make_embed(
        title="Welcome to Support",
        description="Would you like to **claim a purchase** or speak with **staff**?"
    )
    view = TicketButtons()                       # buttons anzeigen
    await channel.send(embed=embed, view=view)   # embed + buttons senden
    await view.wait()                            # auf klick warten

    if view.result == "staff":
        embed = make_embed(
            title="Staff Contacted",
            description=f"<@{STAFF_ID}> has been notified and will be with you shortly.",
            color=BRAND_COLOR
        )
        await channel.send(embed=embed)  # staff benachrichtigen
        return

    if view.result != "claim":  # timeout oder kein klick
        return

    # ── Schritt 2: Invoice ID eingeben ──
    embed = make_embed(
        title="Enter Invoice ID",
        description="Please type your **Invoice ID** below so we can process your claim."
    )
    await channel.send(embed=embed)  # aufforderung senden

    def check(message):
        return message.channel == channel and not message.author.bot  # nur nachrichten im ticket von echten usern

    while True:
        try:
            msg = await bot.wait_for("message", check=check, timeout=300)  # max 5 min warten
            invoice_id  = msg.content   # invoice id aus nachricht
            ticket_user = msg.author    # user der das ticket erstellt hat

            # ── Schritt 3: Invoice ID bestätigen ──
            embed = make_embed(
                title="Confirm Invoice ID",
                description=f"You entered:\n```\n{invoice_id}\n```\nIs this correct?"
            )
            view = ConfirmView()                          # ja/nein buttons
            await channel.send(embed=embed, view=view)   # fragen ob id korrekt
            await view.wait()                            # auf antwort warten

            if view.result != "yes":  # nein oder timeout
                await channel.send(embed=make_embed(
                    title="Try Again",
                    description="Please send the correct Invoice ID:",
                    color=ERROR_COLOR
                ))
                continue  # von vorne anfangen

            # ── Schritt 4: Invoice von API abrufen ──
            try:
                await channel.send(embed=make_embed(
                    title="Processing...",
                    description=f"Looking up Invoice `{invoice_id}`..."
                ))

                data = check_invoice(invoice_id=invoice_id)  # api call

                if not data.get('items'):  # items liste leer oder nicht vorhanden
                    await channel.send(embed=make_embed(
                        title="Invalid Invoice",
                        description="This invoice contains no items. Please contact staff.",
                        color=ERROR_COLOR
                    ))
                    break

                product_name = data['items'][0]['product']['name']          # produktname z.b. "Crunchyroll"
                variant_name = data['items'][0]['variant']['name']          # variante z.b. "Default"
                status       = data['status']                               # z.b. "completed"
                email        = data['email']                                # käufer email
                price        = data['price']                                # z.b. "1.05"
                currency     = data['currency']                             # z.b. "USD"

                # ── Schritt 5: Status prüfen ──
                if status != "completed":  # nur completed invoices erlaubt
                    await channel.send(embed=make_embed(
                        title="Invoice Not Completed",
                        description=f"This invoice has the status **{status}**.\nOnly completed invoices can be claimed.",
                        color=ERROR_COLOR
                    ))
                    break  # loop beenden

                # ── Schritt 6: Invoice details zeigen & bestätigen ──
                embed = make_embed(title="Invoice Found!", color=SUCCESS_COLOR)
                embed.add_field(name="Product",  value=product_name,          inline=True)  # produktname
                embed.add_field(name="Variant",  value=variant_name,          inline=True)  # variante
                embed.add_field(name="Status",   value=status,                inline=True)  # status
                embed.add_field(name="Price",    value=f"{price} {currency}",  inline=True)  # preis
                embed.add_field(name="Email",    value=email,                  inline=True)  # email

                view = ConfirmView()                          # bestätigung buttons
                await channel.send(embed=embed, view=view)   # invoice details senden
                await view.wait()                            # warten

                if view.result != "yes":  # nein gedrückt
                    await channel.send(embed=make_embed(
                        title="Try Again",
                        description="Please send the correct Invoice ID:",
                        color=ERROR_COLOR
                    ))
                    continue  # von vorne

                # ── Schritt 7: Produkt aus Supabase holen ──
                product = check_supabase(product_name, invoice_id)  # verfügbarkeit prüfen
                print(f"DEBUG: product = {product}")
                print("API PRODUCT:", product_name)

                if product is None:  # out of stock
                    await channel.send(embed=make_embed(
                        title="Out of Stock",
                        description=f"Sorry, **{product_name}** is currently out of stock.\n<@{STAFF_ID}> has been notified.",
                        color=ERROR_COLOR
                    ))
                    break

                if product is True:  # bereits geclaimed
                    await channel.send(embed=make_embed(
                        title="Already Claimed",
                        description="This invoice has already been used to claim a product.",
                        color=ERROR_COLOR
                    ))
                    break

                # ── Schritt 8: Produkt senden (DB zuerst, dann DM) ──
                product_embed = make_embed(
                    title="Your Product",
                    description="Here are your login details — keep them safe!",
                    color=SUCCESS_COLOR
                )
                product_embed.add_field(name="Product",  value=product['product'],  inline=False)  # produktname
                product_embed.add_field(name="Email",    value=product['email'],    inline=False)  # login email
                product_embed.add_field(name="Password", value=product['password'], inline=False)  # login passwort

                try:
                    supabase.table("claimed_invoices").insert({"invoice": invoice_id}).execute()  # invoice als claimed markieren (unique constraint verhindert doppelt)
                    res = supabase.table("products").update({"claimed": True}).eq("id", product['id']).execute()
                    print("UPDATE RESULT:", res.data)  # produkt als vergeben markieren
                    await ticket_user.send(embed=product_embed)  # login daten per DM senden
                    await channel.send(embed=make_embed(
                        title="Product Delivered!",
                        description="Your login details have been sent to your DMs ✉️",
                        color=SUCCESS_COLOR
                    ))
                except discord.Forbidden:  # user hat DMs deaktiviert
                    await channel.send(embed=make_embed(
                        title="Could Not Send DM",
                        description="We couldn't send you a DM. Please enable DMs from server members and open a new ticket.",
                        color=ERROR_COLOR
                    ))
                except Exception as e:  # unbekannter fehler beim senden
                    print(f"DEBUG ERROR: {e}")
                    await channel.send(embed=make_embed(
                        title="Delivery Error",
                        description=f"Something went wrong while delivering your product. Please contact staff.",
                        color=ERROR_COLOR
                    ))
                break  # fertig, loop beenden

            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:  # invoice nicht gefunden
                    await channel.send(embed=make_embed(
                        title="Invoice Not Found",
                        description="We couldn't find that Invoice ID. Please try again:",
                        color=ERROR_COLOR
                    ))
                    continue  # nochmal fragen
                else:
                    await channel.send(embed=make_embed(
                        title="Error",
                        description=f"Something went wrong: `{e}`",
                        color=ERROR_COLOR
                    ))
                    break

        except asyncio.TimeoutError:  # user hat zu lange gebraucht
            await channel.send(embed=make_embed(
                title="Timed Out",
                description="You took too long to respond. Please open a new ticket.",
                color=ERROR_COLOR
            ))
            break


bot.run(token)  # bot starten