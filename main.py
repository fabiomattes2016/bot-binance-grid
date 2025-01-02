import json
import os
import time
from decimal import Decimal
from datetime import datetime
from termcolor import colored
from binance.client import Client
from dotenv import load_dotenv  


load_dotenv()

class CryptoTradeBot:
    def __init__(self, api_key, api_secret):
        self.client = Client(api_key, api_secret, testnet=True)
        self.operations = []  # Operations queue
        self.load_operations()
        self.simulated = True  # Simulated trading mode
        self.usdt_balance, self.btc_balance = Decimal("0"), Decimal("0")
        
        # Initial balances
        if self.simulated:
            self.usdt_balance, self.btc_balance = self.get_fake_balances()
        else:  
            self.usdt_balance, self.btc_balance = self.fetch_balances()
        
        # Adjustable thresholds
        self.profit_threshold = Decimal("0.005")  # 0.5% profit
        self.drop_threshold = Decimal("0.002")  # 0.2% drop
        self.trade_amount_btc = Decimal("0.001")

        # Ensure initial operation exists
        if not self.operations:
            if len(self.operations) == 0:
                self.initialize_operations()
                
    def get_fake_balances(self):
        usdt_balance = Decimal("0")
        btc_balance = Decimal("0")
        
        try:
            with open("fake_balances.json", "r", encoding="utf-8") as file:
                data = json.load(file)
                
            usdt_balance = Decimal(data["usdt"])
            btc_balance = Decimal(data["btc"])
            
            return usdt_balance, btc_balance
        except Exception as e:
            self.log(f"Erro ao carregar os saldos falsos.: {e}", "red")
            return None, None
        
    def update_fake_balances(self, operation, amount_usdt, amount_btc):
        usdt = 0
        btc = 0
        
        with open("fake_balances.json", "r", encoding="utf-8") as file:
            data = json.load(file)

        if operation == "buy":
            usdt = data["usdt"]
            btc = data["btc"]
            
            data["usdt"] = usdt - float(amount_usdt)
            data["btc"] = btc + float(amount_btc)
        elif operation == "sell":
            usdt = data["usdt"]
            btc = data["btc"]
            
            data["usdt"] = usdt + float(amount_usdt)
            data["btc"] = float(amount_btc) - btc
        else:
            self.log("Operação inválida. Use 'compra' ou 'venda'.", "red")
            
        with open("fake_balances.json", "w", encoding="utf-8") as file:
            json.dump(data, file, indent=4)
           
    def load_operations(self):
        if os.path.exists("operations.json"):
            with open("operations.json", "r") as file:
                self.operations = json.load(file)

    def save_operations(self):
        with open("operations.json", "w") as file:
            json.dump(self.operations, file, indent=4)

    def log(self, message, color="white"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_message = f"[{timestamp}] {message}"
        
        # Exibe o log no console
        print(colored(formatted_message, color))
        
        # Salva o log em um arquivo
        with open("bot_logs.log", "a") as log_file:
            log_file.write(formatted_message + "\n")

    def fetch_current_price(self, symbol="BTCUSDT"):
        ticker = self.client.get_ticker(symbol=symbol)
        return Decimal(ticker["lastPrice"])

    def fetch_balances(self):
        usdt_balance = Decimal("0")
        btc_balance = Decimal("0")
        current_price = Decimal("0")
        
        if self.simulated:
            self.usdt_balance, self.btc_balance = self.get_fake_balances()
            usdt_balance = self.usdt_balance
            btc_balance = self.btc_balance
            current_price = self.fetch_current_price()
        else:
            account_info = self.client.get_account()
            balances = {asset["asset"]: Decimal(asset["free"]) for asset in account_info["balances"] if Decimal(asset["free"]) > 0}
            usdt_balance = balances.get("USDT", Decimal("0"))
            self.usdt_balance = balances.get("USDT", Decimal("0"))
            self.btc_balance = balances.get("BTC", Decimal("0"))
            btc_balance = balances.get("BTC", Decimal("0"))
            current_price = self.fetch_current_price()
        
        self.log(f"USDT Balance: {round(float(usdt_balance), 2)}", "blue")
        self.log(f"BTC Balance: {round(float(btc_balance), 4)}", "yellow")
        self.log(f"Current BTC Price: {round(float(current_price), 2)} USDT", "green")
        return usdt_balance, btc_balance

    def buy(self, price):
        if self.usdt_balance >= price * self.trade_amount_btc:
            self.usdt_balance -= price * self.trade_amount_btc
            
            order = self.client.create_order(
                symbol="BTCUSDT",
                side="BUY",
                type="MARKET",
                quantity=float(self.trade_amount_btc)
            )
            
            self.log(f"Buy Order Placed: {order["orderId"]}", "green")
            
            target_threshold = price * self.profit_threshold
            target = price + target_threshold
            
            operation = {"price": str(price), "amount": str(self.trade_amount_btc), "target": str(target)}
            
            self.operations.append(operation)
            
            self.save_operations()
            
            self.update_fake_balances("buy", price * self.trade_amount_btc, self.trade_amount_btc)
        else:
            self.log("Insufficient USDT balance to buy.", "red")

    def sell(self, operation_index, price):
        if self.btc_balance < Decimal(self.operations[operation_index]["amount"]):
            self.log("Insufficient BTC balance to sell.", "red")
            return
        else:
            operation = self.operations[operation_index]
            amount = Decimal(operation["amount"])
            self.usdt_balance += price * amount
            order = self.client.create_order(
                symbol="BTCUSDT",
                side="SELL",
                type="MARKET",
                quantity=float(amount)
            )
            self.log(f"Sell Order Placed: {order["orderId"]}", "green")
            del self.operations[operation_index]
            self.save_operations()
            self.update_fake_balances("sell", price * amount, amount)

    def evaluate_operations(self):
        current_price = self.fetch_current_price()
        
        for i, operation in enumerate(self.operations):
            buy_price = Decimal(operation["target"])
            amount = Decimal(operation["amount"])

            # Check for profit threshold
            if current_price >= buy_price:
                self.sell(i, current_price)
                break

            # Check for drop threshold
            if len(self.operations) == 0:
                self.buy(current_price)
                time.sleep(2)  # Avoid API rate limits
                
            if current_price <= buy_price * (1 - self.drop_threshold):
                self.buy(current_price)
                time.sleep(2)  # Avoid API rate limits

    def initialize_operations(self):
        current_price = self.fetch_current_price()
        self.log("Initializing first operation...", "cyan")
        
        self.buy(current_price)

    def run(self):
        try:
            while True:
                now = datetime.now()
                
                if now.hour == 22 and now.minute >= 30 or (now.hour >= 23 and now.hour < 1):
                    self.log("Reinicialização da conexão.", "red")
                    time.sleep(60)
                    continue
                
                self.usdt_balance, self.btc_balance = self.fetch_balances()
                
                self.evaluate_operations()
                
                self.log(f"Current USDT balance: {round(float(self.usdt_balance), 2)}", "blue")
                self.log(f"Current BTC balance: {round(float(self.btc_balance), 4)}", "yellow")
                self.log(f"Operations in queue: {len(self.operations)}", "cyan")
                
                time.sleep(10)  # Check every 10 seconds
        except KeyboardInterrupt:
            self.log("Bot stopped.", "red")
        except Exception as e:
            self.log(f"Error: {str(e)}", "red")

if __name__ == "__main__":
    # Replace these with your Binance API keys
    API_KEY = os.getenv("API_KEY")
    API_SECRET = os.getenv("API_SECRET")

    bot = CryptoTradeBot(api_key=API_KEY, api_secret=API_SECRET)
    bot.run()
    