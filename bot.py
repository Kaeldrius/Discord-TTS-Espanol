import asyncio, json, os, tempfile, logging
from pathlib import Path
import discord, edge_tts
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
load_dotenv()
logging.basicConfig(level=logging.INFO)
DB=Path('config.json')
try: data=json.loads(DB.read_text(encoding='utf8'))
except (OSError,ValueError): data={'users':{},'guilds':{}}
data.setdefault('users',{});data.setdefault('guilds',{})
def save(): DB.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
def user(id): return data['users'].setdefault(str(id),{'voice':'es-ES-ElviraNeural','locale':'es-ES','rate':0,'pitch':0})
def guild(id): return data['guilds'].setdefault(str(id),{'auto':None})
intents=discord.Intents.default();intents.message_content=True
bot=commands.Bot(command_prefix='!',intents=intents)
voices=[];queues={};workers={}
async def load_voices():
 global voices
 if not voices: voices=sorted((v for v in await edge_tts.list_voices() if v['Locale'].startswith('es-')),key=lambda v:v['ShortName'])
 return voices
async def connect(i):
 if not i.guild or not isinstance(i.user,discord.Member) or not i.user.voice: return None
 vc=i.guild.voice_client;ch=i.user.voice.channel
 if vc and vc.is_connected():
  if vc.channel!=ch: await vc.move_to(ch)
 else: vc=await ch.connect()
 # Por defecto, leer el chat integrado del canal de voz conectado.
 # /auto permite elegir otro canal o desactivar esta función.
 guild(i.guild.id)['auto']=ch.id
 save()
 return vc
async def worker(gid):
 q=queues[gid]
 while True:
  item=await q.get();path=None
  try:
   g=bot.get_guild(gid);vc=g.voice_client if g else None
   if not vc or not vc.is_connected(): continue
   with tempfile.NamedTemporaryFile(suffix='.mp3',delete=False) as f: path=f.name
   await edge_tts.Communicate(item['text'],item['voice'],rate=f"{item['rate']:+d}%",pitch=f"{item['pitch']:+d}Hz").save(path)
   loop=asyncio.get_running_loop();done=loop.create_future()
   def after(err): loop.call_soon_threadsafe(lambda: None if done.done() else done.set_result(err))
   vc.play(discord.FFmpegPCMAudio(path,executable=os.getenv('FFMPEG_PATH','ffmpeg')),after=after)
   err=await done
   if err: logging.error('Audio: %s',err)
  except Exception: logging.exception('Error de voz')
  finally:
   if path:
    try: os.unlink(path)
    except OSError: pass
   q.task_done()
def enqueue(gid,uid,text):
 q=queues.setdefault(gid,asyncio.Queue(maxsize=40))
 if q.full():return False
 c=user(uid);q.put_nowait(dict(text=text[:500],voice=c['voice'],rate=c['rate'],pitch=c['pitch']))
 if gid not in workers or workers[gid].done():workers[gid]=asyncio.create_task(worker(gid))
 return True
@bot.event
async def setup_hook():
 try:await load_voices()
 except Exception:logging.exception('Lista de voces no disponible')
 await bot.tree.sync()
@bot.event
async def on_ready():print('Bot conectado:',bot.user)
@bot.tree.command(description='Entrar en tu canal de voz')
@app_commands.guild_only()
async def join(i:discord.Interaction):
 await i.response.defer(ephemeral=True)
 try:vc=await connect(i);msg=f'Conectado a {vc.channel.mention}' if vc else 'Entra primero en un canal de voz.'
 except Exception:msg='No pude conectar. Revisa permisos de voz.'
 await i.followup.send(msg,ephemeral=True)
@bot.tree.command(description='Salir del canal y limpiar cola')
@app_commands.guild_only()
async def leave(i:discord.Interaction):
 vc=i.guild.voice_client
 if not vc:return await i.response.send_message('No estoy conectado.',ephemeral=True)
 if not isinstance(i.user,discord.Member) or not i.user.voice or i.user.voice.channel!=vc.channel:return await i.response.send_message('Debes estar en el mismo canal de voz.',ephemeral=True)
 task=workers.pop(i.guild.id,None)
 if task:task.cancel()
 queues.pop(i.guild.id,None);vc.stop();await vc.disconnect(force=True)
 await i.response.send_message('Desconectado.',ephemeral=True)
async def suggest(i:discord.Interaction,current:str):
 try:await load_voices()
 except Exception:return []
 found=[v for v in voices if current.lower() in v['ShortName'].lower()]
 found.sort(key=lambda v:(v['Locale']!=user(i.user.id)['locale'],v['ShortName']))
 return [app_commands.Choice(name=f"{v['ShortName']} ({'F' if v['Gender']=='Female' else 'M'})",value=v['ShortName']) for v in found[:25]]
@bot.tree.command(description='Elige tu voz masculina o femenina')
@app_commands.autocomplete(nombre=suggest)
async def voz(i:discord.Interaction,nombre:str):
 try:await load_voices()
 except Exception:return await i.response.send_message('No se pudo consultar las voces.',ephemeral=True)
 v=next((v for v in voices if v['ShortName'].lower()==nombre.lower()),None)
 if not v:return await i.response.send_message('Voz no disponible. Usa las sugerencias.',ephemeral=True)
 user(i.user.id).update(voice=v['ShortName'],locale=v['Locale']);save()
 await i.response.send_message(f"Voz seleccionada: {v['ShortName']}",ephemeral=True)
@bot.tree.command(description='Elige país: ES, MX, AR, CO, CL, PE, etc.')
async def idioma(i:discord.Interaction,pais:str):
 try:await load_voices()
 except Exception:return await i.response.send_message('No se pudo consultar las voces.',ephemeral=True)
 locale='es-'+pais.upper().removeprefix('ES-');vs=[v for v in voices if v['Locale']==locale]
 if not vs:return await i.response.send_message('País no disponible. Consulta /paises.',ephemeral=True)
 user(i.user.id).update(locale=locale,voice=vs[0]['ShortName']);save()
 await i.response.send_message(f'Idioma {locale}; voz {vs[0]["ShortName"]}. Usa /voz para cambiar.',ephemeral=True)
@bot.tree.command(description='Lista todos los países disponibles')
async def paises(i:discord.Interaction):
 try:await load_voices();s=', '.join(sorted(set(v['Locale'] for v in voices)))
 except Exception:s='No se pudieron consultar las voces.'
 await i.response.send_message(s,ephemeral=True)
@bot.tree.command(description='Cambia tu velocidad entre -50 y +100 por ciento')
async def velocidad(i:discord.Interaction,porcentaje:app_commands.Range[int,-50,100]):
 user(i.user.id)['rate']=porcentaje;save();await i.response.send_message(f'Velocidad {porcentaje:+d}%',ephemeral=True)
@bot.tree.command(description='Cambia tu tono entre -100 y +100 Hz')
async def pitch(i:discord.Interaction,hz:app_commands.Range[int,-100,100]):
 user(i.user.id)['pitch']=hz;save();await i.response.send_message(f'Tono {hz:+d} Hz',ephemeral=True)
@bot.tree.command(description='Lee un mensaje de hasta 500 caracteres')
@app_commands.guild_only()
async def tts(i:discord.Interaction,texto:app_commands.Range[str,1,500]):
 await i.response.defer(ephemeral=True)
 try:vc=await connect(i)
 except Exception:vc=None
 if not vc:return await i.followup.send('Entra en un canal de voz y revisa los permisos.',ephemeral=True)
 await i.followup.send('Añadido a la cola.' if enqueue(i.guild.id,i.user.id,texto) else 'Cola llena.',ephemeral=True)
@bot.tree.command(description='Activa o desactiva la lectura automática de un canal')
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def auto(i:discord.Interaction,activar:bool,canal:discord.TextChannel|discord.VoiceChannel|None=None):
 if not i.user.guild_permissions.manage_guild:return await i.response.send_message('Necesitas Gestionar servidor.',ephemeral=True)
 if activar:
  try:vc=await connect(i)
  except Exception:vc=None
  if not vc:return await i.response.send_message('Primero entra en un canal de voz.',ephemeral=True)
 guild(i.guild.id)['auto']=(canal.id if canal else vc.channel.id) if activar else None;save()
 await i.response.send_message('Lectura automática activada en el chat del canal seleccionado.' if activar else 'Lectura automática desactivada.',ephemeral=True)
@bot.event
async def on_message(m:discord.Message):
 if m.author.bot or not m.guild or guild(m.guild.id)['auto']!=m.channel.id:return
 vc=m.guild.voice_client
 if not vc or not vc.is_connected() or not isinstance(m.author,discord.Member) or not m.author.voice or m.author.voice.channel!=vc.channel:return
 if m.content and not m.content.startswith(('!','/')):enqueue(m.guild.id,m.author.id,m.content)
if __name__=='__main__':
 token=os.getenv('DISCORD_TOKEN')
 if not token:raise SystemExit('Configura DISCORD_TOKEN en .env')
 bot.run(token)
