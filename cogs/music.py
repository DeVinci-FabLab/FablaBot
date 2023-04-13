import discord
from discord.ext import commands
import youtube_dl
import asyncio
import os

ydl_opts = {}
ytdl = youtube_dl.YoutubeDL(ydl_opts)
musics = {}
GUILD_TOKEN = int(os.environ.get("GUILD_TOKEN"))
MY_GUILD = discord.Object(id=GUILD_TOKEN)

class Music(commands.Cog):
      def __init__(self, client: commands.Bot):
            self.client = client
            client.tree.copy_global_to(guild=MY_GUILD)
      class Video:
            def __init__(self, link):
                  if link[0:4]=="http"or link[0:3]=="wwww":
                        video = ytdl.extract_info(link, download=False)
                  else:
                        video = ytdl.extract_info("ytsearch:%s" % link, download=False)['entries'][0]
                        video_format = video["formats"][0]
                        self.url = video["webpage_url"]
                        self.stream_url = video_format["url"]
      def play_song(self,client, queue, song):
            source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(song.stream_url, before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"))

            def next (_):
                  if len(queue) > 0:
                        new_song = queue[0]
                        del queue[0]
                        self.play_song(client, queue, new_song)
                  else:
                        asyncio.run_coroutine_threadsafe(client.disconnect(), self.bot.loop)

            client.play(source, after = next)

      @commands.command()
      async def skip(self,ctx):
        client = ctx.guild.voice_client
        client.stop()
    #    play_song(client, musics[ctx.guild], musics[ctx.guild][0])

      @commands.command()
      async def leave(self,ctx):
            client = ctx.guild.voice_client
            await client.disconnect()
            musics[ctx.guild] = []

      @commands.command()
      async def resume(self,ctx):
            client = ctx.guild.voice_client
            if client.is_paused():
                  client.resume()

      @commands.command()
      async def pause(self,ctx):
            client = ctx.guild.voice_client
            if not client.is_paused():
                  client.pause()

      @commands.command(aliases= ['p'])
      async def play(self,ctx, *url):
            print("play")
            urla = str(" ".join(url))
            client = ctx.guild.voice_client

            if client and client.channel:
                  video = self.Video(urla)
                  musics[ctx.guild].append(video)
      #            embed= discord.Embed(
      #                title="La musique suivante à été ajoutée à la queue:",
      #                description=f"{video['title']}"
      #            )
      #            await ctx.send (embed=embed,delete_after=10)   
            elif ctx.author.voice!=None:
                  channel = ctx.author.voice.channel
                  video = self.Video(urla)
                  musics[ctx.guild]=[]
                  client = await channel.connect()
                  await ctx.send(f"Je lance : {video.url}",delete_after = 15)
                  await ctx.message.delete()
                  self.play_song(client, musics[ctx.guild], video)
            else:
                  embed= discord.Embed(
                  title=f"Utilisateur non connecté",
                  description=f"{ctx.author.mention} Veuillez vous connecter à un salon vocal \n||Cette requête s'arrêtera dans 10 secondes||"
                  )
                  sent=await ctx.send (embed=embed,delete_after=10)        
      

      @commands.hybrid_command(name="test", description="Sends hello!")
      async def test(self, interaction: discord.Interaction):
            await interaction.reply(content="Hello!")

async def setup(client:commands.Bot) -> None:
      await client.add_cog(Music(client))