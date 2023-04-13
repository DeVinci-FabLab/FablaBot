import discord
from discord.ext import commands
from discord import app_commands, Permissions
from dotenv import load_dotenv
from discord.ui import Button , View , Select
import os
from datetime import datetime
import asyncio 
import time


#import for selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

GUILD_TOKEN = int(os.environ.get("GUILD_TOKEN"))
MY_GUILD = discord.Object(id=GUILD_TOKEN)
CURRENT_TIME = datetime.now().strftime("%Y/%m/%d, %H:%M:%S")
EMAIL = os.environ.get("MY_GMAIL_ACCOUNT")
PASSWORD = os.environ.get("MY_GMAIL_PASSWORD")



form= {"Informatique":"INFO",
           "Electronique":"ELEC",
           "Mécanique":"MECA",
           "Couture":"COUTURE",
           "-1":"-1"}
           
async def setup(client:commands.Bot) -> None:
         await client.add_cog(Formation(client))


class latex:
   def load_driver(self):
      print("loading driver")
      options = Options()
      options.add_argument('--no-sandbox')
      options.add_argument("window-size=1920,1080")
      options.add_argument('--disable-dev-shm-usage')
      options.add_argument('--headless')
      driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
      
      driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
      driver.get("https://www.overleaf.com/login")

      driver.get("https://www.overleaf.com/auth/orcid?intent=sign_in")

      WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CLASS_NAME, 'mat-button-wrapper')))
      driver.find_elements(By.CSS_SELECTOR, "input[formcontrolname='username']")[0].send_keys(EMAIL)
      driver.find_elements(By.CSS_SELECTOR, "input[formcontrolname='password']")[0].send_keys(PASSWORD)

      #click on button with type submit
      driver.find_elements(By.CSS_SELECTOR, "button[type='submit']")[0].click()
      try:
         WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder='Search projects…']")))
      except:
         print("errorduring login")
      return(driver)
   def create_latex(self,name):
      print("creating latex")
      driver=self.load_driver()
      time.sleep(2)
      elem= self.find_latex("Template FORMATION 2023",driver)
      time.sleep(2)
      elem = elem.find_element(By.CSS_SELECTOR, "td.dash-cell-actions").find_elements(By.CSS_SELECTOR, "div")[0].find_elements(By.CSS_SELECTOR, "button")[0]
      time.sleep(3)
      elem.click()
      time.sleep(0.5)
      INPUT=driver.find_element(By.XPATH, "//input[@placeholder='New Project Name']")
      INPUT.clear()
      INPUT.send_keys(name)
      driver.find_element(By.XPATH, "//button[text()='Copy' and @type='submit']").click()
      driver.refresh()
      driver.get("https://www.overleaf.com/project")
      return(driver)
   def find_latex(self,name,driver):
      print("finding latex")
      time.sleep(2)
      #find element with xpath with placeholder="Search projects...", and type='text'
      NameInput=driver.find_elements(By.XPATH, "//input[@placeholder='Search projects…' and @aria-label='Search projects…' and @type='text'] ")[0]
      NameInput.clear()
      NameInput.send_keys(name)
      time.sleep(0.5)
      data=driver.find_elements(By.CSS_SELECTOR, "tbody tr")
      value=[i.find_elements(By.CSS_SELECTOR, "td.dash-cell-name")[0].find_element(By.CSS_SELECTOR, "a").text for i in data].index(name)
      #get dash-cell-actions in data[value]
      return(data[value])

   def enter_latex(self,name,driver):
      print("entering latex")
      self.find_latex(name,driver)
      [i.find_element(By.CSS_SELECTOR, "a") for i in driver.find_elements(By.CSS_SELECTOR, "td.dash-cell-name") if i.find_element(By.CSS_SELECTOR, "a").text==name][0].click() #.find_element(By.CSS_SELECTOR, "a").click()

   def get_link(self,name,driver):
      print("getting link")
      self.enter_latex(name,driver)
      #get the second button f the list
      time.sleep(5)
      driver.find_elements(By.CSS_SELECTOR, "div.toolbar-right")[0].find_elements(By.CSS_SELECTOR, "button")[1].click()
      WebDriverWait(driver,30).until(EC.presence_of_element_located((By.XPATH, "//button [@class='btn-inline-link btn btn-link']")))
      button=driver.find_element(By.XPATH, "//button [@class='btn-inline-link btn btn-link']")

      button.click() if  button.text=="Turn on link sharing" else print("already sharing")    
      driver.find_elements(By.CSS_SELECTOR, "pre.access-token")
      WebDriverWait(driver,30).until(EC.presence_of_element_located((By.CSS_SELECTOR, "pre.access-token")))
      driver.find_elements(By.CSS_SELECTOR, "pre.access-token")
      return [i.text for i in driver.find_elements(By.CSS_SELECTOR, "pre.access-token")]


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

class MyFormation(app_commands.Group):
      
      @app_commands.command(name="add_formation", description="Visualise les formations")
      @app_commands.describe(formation='formation chosen')
      @commands.has_role("Pôle Formation")
      @app_commands.choices(formation=[
         app_commands.Choice(name="Informatique", value=0),
         app_commands.Choice(name="Electronique", value=1),
         app_commands.Choice(name="Mécanique", value=2),
         app_commands.Choice(name="Couture", value=3),
         app_commands.Choice(name="-1", value=4),
      ])
      async def add_formation(self, interaction: discord.Interaction, formation : discord.app_commands.Choice[int], invisible : bool=False):
         print(f"add_formation : {interaction.user.name}:{interaction.user.id} {formation.name} {invisible}")
         await interaction.response.send_message(f"{CURRENT_TIME} formation chosen : {formation.name}", ephemeral=invisible, delete_after=5)
         options=generateGraph(form[formation.name])
         await asyncio.sleep(5)
         print(options)
         select = mySelect(options=options)
         view= myView(select)
         embed = myEmbed({"title":"Title", "description":"Desc", "color":0xffffff}) #creates embed
         file = embed.add_image("./cogs/data/File.png") 
         print(2)
         await interaction.followup.send(file=file,embed=embed,view=view, ephemeral=invisible) #,  

      @app_commands.command(name="add_latex", description="Visualise les formations")
      @commands.has_role("Pôle Formation")
      async def add_latex(self, interaction: discord.Interaction, name: str):
         print(f"add_latex : {interaction.user.name}:{interaction.user.id} {name}")
         await interaction.response.send_message(f"{CURRENT_TIME} formation created : {name}", delete_after=5)
         Latex=latex()
         driver=Latex.create_latex(name)
         await interaction.followup.send(Latex.get_link(name,driver))
         driver.close()


class Formation(commands.Cog):
      def __init__(self, client: commands.Bot):
         myformation=MyFormation(name="formation", description="Commandes autour des formations")
         self.client = client
         client.tree.add_command(myformation)
         client.tree.copy_global_to(guild=MY_GUILD)


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
