import sys
import traceback
sys.path.append(r"c:\Users\tanush.angrish\Desktop\Tanush\LexAI\apps\api")

from app.services.indiacode_scraper import search_and_get_candidates

try:
    candidates = search_and_get_candidates("constitution", top_n=3)
    print(candidates)
except Exception as e:
    traceback.print_exc()
