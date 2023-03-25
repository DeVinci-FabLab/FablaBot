import discord
from discord.ext import commands
from discord import app_commands, Permissions
from dotenv import load_dotenv
from discord.ui import Button , View , Select
import os
import time
import asyncio

GUILD_TOKEN = int(os.environ.get("GUILD_TOKEN"))
MY_GUILD = discord.Object(id=GUILD_TOKEN)



overwrite=[discord.PermissionOverwrite() for i in range(3)]
overwrite[0].send_messages = True
overwrite[0].read_messages = True
overwrite[1].send_messages = False
overwrite[1].read_messages = True
overwrite[2].send_messages = False
overwrite[2].read_messages = False

form= {"Informatique":"INFO",
           "Electronique":"ELEC",
           "Mécanique":"MECA",
           "Couture":"COUTURE",
           "-1":"-1"}

class formation(commands.Cog):
      def __init__(self, client: commands.Bot):
         self.client = client
         client.tree.copy_global_to(guild=MY_GUILD)

      @commands.hybrid_command(name="formation", with_app_command=True, description="Visualise les formations")
      @app_commands.describe(formation='formation chosen')
      @commands.has_role("Formateur")
      @app_commands.choices(formation=[
         app_commands.Choice(name="Informatique", value=0),
         app_commands.Choice(name="Electronique", value=1),
         app_commands.Choice(name="Mécanique", value=2),
         app_commands.Choice(name="Couture", value=3),
         app_commands.Choice(name="-1", value=4),
      ])
      async def formation(self, ctx: commands.Context, formation : discord.app_commands.Choice[int], visible : bool=True):
         #await ctx.send(f"formation chosen : {formation.name}", ephemeral=True)
         #read the file formation[formation.name]
         f= open(f"./cogs/data/{form[formation.name]}.puml", "r")   #{formation[str(formation.name)]}
         await ctx.reply(f.read(), ephemeral=visible)

      @commands.hybrid_command(name="ajouterformation", with_app_command=True, description="Visualise les formations")
      @app_commands.describe(formation='formation chosen')
      @commands.has_role("Formateur")
      @app_commands.choices(formation=[
         app_commands.Choice(name="Informatique", value=0),
         app_commands.Choice(name="Electronique", value=1),
         app_commands.Choice(name="Mécanique", value=2),
         app_commands.Choice(name="Couture", value=3),
         app_commands.Choice(name="-1", value=4),
      ])
      async def ajouterformation(self, ctx: commands.Context, formation : discord.app_commands.Choice[int], visible : bool=False):
         await ctx.send(f"formation chosen : {formation.name}", ephemeral=visible, delete_after=5)
         options=generateGraph(form[formation.name])
         await asyncio.sleep(5)
         select = mySelect(options=options)

         view= myView(select)
         embed = myEmbed({"title":"Title", "description":"Desc", "color":0xffffff}) #creates embed
         file = embed.add_image("./cogs/data/File.png") 
         await ctx.send(file=file, embed=embed, view=view, ephemeral=visible) # 

      @commands.hybrid_command(name="clear", with_app_command=True, description="clear channel")
      async def clear(self, ctx: commands.Context):
         await ctx.channel.purge(limit=100)
         await ctx.send("Channel cleared", delete_after=5)




async def setup(client:commands.Bot) -> None:
         await client.add_cog(formation(client))

class mySelect(Select):
   def __init__(self, options: list) -> None:
      super().__init__(
         placeholder="make the selection of the parent function",
         min_values= 0, 
         max_values=len(options) ,
         options=options,)
   async def callback(self, interaction: discord.Interaction):
      self.disabled=True
      await interaction.response.edit_message(view=myView(self))
      await interaction.followup.send(f"You've chosen {' '.join(self.values)}")

      
      # await interaction.response.defer()

class myView(View, mySelect):
   def __init__(self, select: mySelect) -> None:
      super().__init__(timeout=5)
      self.add_item(select)
      self.timeout = None

class myEmbed(discord.Embed):
   def __init__(self, values : dict) -> None:
      super().__init__(
                     title=values["title"] if ("title" in values.keys()) else None,
                     description=values["description"] if ("description" in values.keys()) else None, 
                     color=values["color"] if ("color" in values.keys()) else None,
                     url=values["url"] if ("url" in values.keys()) else None)
   def add_image(self, path : str):
      file = discord.File(path, filename="image.png")
      self.set_image(url="attachment://image.png")
      return file


def generateGraph(title: str):
   fileInputs= open(f"./cogs/data/{title}.puml", "r", encoding='utf-8')   #{formation[str(formation.name)]}
   file= open(f"./cogs/data/File.puml", "r",  encoding='utf-8')
   fileInputs=fileInputs.read().split("\n")
   file=file.read().split("\n")
   #add f to file on the -2 position
   file[-2:-2]=fileInputs
   with open(f"./cogs/data/File2.puml", "w",  encoding='utf-8') as file2:
      file2.write('\n'.join(file))
   os.system(f"plantuml ./cogs/data/File2.puml")
   liste=[i[6:] for i in fileInputs[1:-1] if i.startswith("State")]
   liste=list(set(liste))
   return [discord.SelectOption(label=str(liste[i])) for i in range(len(liste))]
