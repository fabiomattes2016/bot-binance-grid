import json
import os
import time
from decimal import Decimal, getcontext
from datetime import datetime
from termcolor import colored
from binance.client import Client
from dotenv import load_dotenv

# Precisão Decimal
getcontext().prec = 18

load_dotenv()

class CryptoTradeBot:
    def __init__(self, api_key, api_secret):
        # Testnet flag (puxa do .env, default True)
        self.testnet = os.getenv("BINANCE_TESTNET", "True").lower() in ("1", "true", "yes")
        self.client = Client(api_key, api_secret, testnet=self.testnet)

        self.operations = []  # queue de operações (compra aberta que espera venda)
        self.load_operations()

        self.simulated = os.getenv("SIMULATED", "True").lower() in ("1", "true", "yes")

        # trade pair ex: BTCUSDT
        self.trade_pair = os.getenv("TRADE_PAIR", "BTCUSDT").upper()
        self.fiat = self.trade_pair[3:]
        self.crypto = self.trade_pair[:3]

        # Balances
        self.usdt_balance, self.btc_balance = Decimal("0"), Decimal("0")

        # Thresholds e parâmetros
        self.profit_threshold = Decimal(os.getenv("PROFIT_THRESHOLD", "0.005"))  # 0.5% padrão
        self.drop_threshold = Decimal(os.getenv("DROP_THRESHOLD", "0.02"))  # 2% queda para DCA por padrão
        self.trade_amount_btc = Decimal(os.getenv("TRADE_AMOUNT", "0.0001"))

        # Estratégia: basic, breakout, sma, rsi, dca
        self.strategy = os.getenv("STRATEGY", "basic").lower()
        
        self.budget_limit = Decimal(os.getenv("BUDGET_LIMIT", "0"))  # 0 = sem limite

        # Inicializa saldos
        if self.simulated:
            self.usdt_balance, self.btc_balance = self.get_fake_balances()
        else:
            self.usdt_balance, self.btc_balance = self.fetch_balances()

        # Garantir operação inicial
        if not self.operations:
            self.initialize_operations()

    # -------------------------
    # Utilitários de arquivo
    # -------------------------
    def load_operations(self):
        if os.path.exists("operations.json"):
            try:
                with open("operations.json", "r", encoding="utf-8") as f:
                    ops = json.load(f)
                    # garante tipos corretos
                    self.operations = ops
            except Exception as e:
                self.log(f"Falha ao carregar operations.json: {e}", "red")
                self.operations = []

    def save_operations(self):
        try:
            with open("operations.json", "w", encoding="utf-8") as f:
                json.dump(self.operations, f, indent=4)
        except Exception as e:
            self.log(f"Erro ao salvar operations.json: {e}", "red")

    # -------------------------
    # Logging
    # -------------------------
    def log(self, message, color="white"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted = f"[{timestamp}] {message}"
        print(colored(formatted, color))
        try:
            with open("bot_logs.log", "a", encoding="utf-8") as log_file:
                log_file.write(formatted + "\n")
        except:
            pass

    # -------------------------
    # Fake balances (simulação)
    # -------------------------
    def get_fake_balances(self):
        # cria arquivo padrão caso não exista
        if not os.path.exists("fake_balances.json"):
            template = {"usdt": 185.00, "btc": 0.0000}
            with open("fake_balances.json", "w", encoding="utf-8") as f:
                json.dump(template, f, indent=4)
            return Decimal(str(template["usdt"])), Decimal(str(template["btc"]))

        try:
            with open("fake_balances.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            return Decimal(str(data.get("usdt", 0))), Decimal(str(data.get("btc", 0)))
        except Exception as e:
            self.log(f"Erro ao carregar fake_balances.json: {e}", "red")
            return Decimal("0"), Decimal("0")

    def update_fake_balances(self, operation, amount_usdt, amount_btc):
        """
        operation: 'buy' ou 'sell'
        amount_usdt: Decimal ou float (valor em fiat envolvido)
        amount_btc: Decimal ou float (quantidade de crypto)
        """
        try:
            with open("fake_balances.json", "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.log(f"Erro ao abrir fake_balances.json para leitura: {e}", "red")
            return

        usdt = Decimal(str(data.get("usdt", 0)))
        btc = Decimal(str(data.get("btc", 0)))

        if operation == "buy":
            data["usdt"] = float(usdt - Decimal(str(amount_usdt)))
            data["btc"] = float(btc + Decimal(str(amount_btc)))
        elif operation == "sell":
            data["usdt"] = float(usdt + Decimal(str(amount_usdt)))
            data["btc"] = float(btc - Decimal(str(amount_btc)))
        else:
            self.log("Operação inválida em update_fake_balances. Use 'buy' ou 'sell'.", "red")
            return

        try:
            with open("fake_balances.json", "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            self.log(f"Erro ao salvar fake_balances.json: {e}", "red")

    # -------------------------
    # Market data
    # -------------------------
    def fetch_current_price(self, symbol=None):
        symbol = symbol or self.trade_pair
        try:
            ticker = self.client.get_symbol_ticker(symbol=symbol)  # retorna {'symbol':..., 'price': '...'}
            price = Decimal(str(ticker.get("price") or ticker.get("lastPrice") or 0))
            return price
        except Exception as e:
            self.log(f"Erro ao buscar preço atual: {e}", "red")
            return Decimal("0")

    def get_candles(self, interval="1m", limit=100):
        """
        Retorna lista de closes (Decimal) das últimas `limit` velas.
        interval ex: "1m", "5m", "1h"
        """
        try:
            klines = self.client.get_klines(symbol=self.trade_pair, interval=interval, limit=limit)
            closes = [Decimal(str(k[4])) for k in klines]
            return closes
        except Exception as e:
            self.log(f"Erro ao buscar candles: {e}", "red")
            return []

    # -------------------------
    # Balances reais (ou simulado)
    # -------------------------
    def fetch_balances(self):
        try:
            if self.simulated:
                usdt_balance, btc_balance = self.get_fake_balances()
            else:
                account = self.client.get_account()
                balances_map = {b["asset"]: Decimal(str(b["free"])) for b in account.get("balances", [])}
                usdt_balance = balances_map.get(self.fiat, Decimal("0"))
                btc_balance = balances_map.get(self.crypto, Decimal("0"))

            self.usdt_balance = usdt_balance
            self.btc_balance = btc_balance

            current_price = self.fetch_current_price()
            self.log(f"{self.fiat} Balance: {round(float(self.usdt_balance), 2)}", "blue")
            self.log(f"{self.crypto} Balance: {round(float(self.btc_balance), 6)}", "yellow")
            self.log(f"Current {self.crypto} Price: {round(float(current_price), 2)} {self.fiat}", "green")

            return usdt_balance, btc_balance
        except Exception as e:
            self.log(f"Erro ao buscar balances: {e}", "red")
            return Decimal("0"), Decimal("0")

    # -------------------------
    # Ordens (simuladas ou reais)
    # -------------------------
    def buy(self, price: Decimal):
        """
        Tenta comprar `self.trade_amount_btc` ao preço `price`.
        """
        try:
            cost = (Decimal(price) * self.trade_amount_btc)
            
            if self.usdt_balance >= cost:
                if self.budget_limit > 0 and (self.usdt_balance - cost) < self.budget_limit:
                    # Simulação: atualiza fake balances e fila operações
                    if self.simulated:
                        self.usdt_balance -= cost
                        self.btc_balance += self.trade_amount_btc
                        self.update_fake_balances("buy", cost, self.trade_amount_btc)
                        order_id = "sim-buy-" + datetime.now().strftime("%Y%m%d%H%M%S")
                    else:
                        order = self.client.create_order(
                            symbol=self.trade_pair,
                            side="BUY",
                            type="MARKET",
                            quantity=float(self.trade_amount_btc)
                        )
                        order_id = order.get("orderId", str(order))

                    # calcula target (preço de venda alvo)
                    target = Decimal(price) * (Decimal("1") + self.profit_threshold)
                    operation = {
                        "price": str(price),
                        "amount": str(self.trade_amount_btc),
                        "target": str(target),
                        "timestamp": datetime.now().isoformat()
                    }
                    self.operations.append(operation)
                    self.save_operations()
                    self.log(f"Buy Order Placed: {order_id} | amount: {self.trade_amount_btc} @ {price}", "green")
                else:
                    self.log(f"Limite de compras alcançado, aguarde vender para comprar novamente!", "red")
            else:
                self.log(f"Saldo insuficiente ({self.fiat}) para comprar. Necessário {float(cost):.8f}", "red")
        except Exception as e:
            self.log(f"Erro na função buy: {e}", "red")

    def sell(self, operation_index: int, price: Decimal):
        """
        Vende a operação na posição operation_index por market no preço atual (simulado/real).
        """
        try:
            if operation_index < 0 or operation_index >= len(self.operations):
                self.log("Índice de operação inválido para venda.", "red")
                return

            operation = self.operations[operation_index]
            amount = Decimal(str(operation["amount"]))

            if self.btc_balance < amount:
                self.log(f"Saldo {self.crypto} insuficiente para vender (necessário {amount}).", "red")
                return

            proceeds = amount * Decimal(price)

            if self.simulated:
                self.btc_balance -= amount
                self.usdt_balance += proceeds
                self.update_fake_balances("sell", proceeds, amount)
                order_id = "sim-sell-" + datetime.now().strftime("%Y%m%d%H%M%S")
            else:
                order = self.client.create_order(
                    symbol=self.trade_pair,
                    side="SELL",
                    type="MARKET",
                    quantity=float(amount)
                )
                order_id = order.get("orderId", str(order))

            # remove operação da fila
            del self.operations[operation_index]
            self.save_operations()
            self.log(f"Sell Order Placed: {order_id} | amount: {amount} @ {price}", "green")
        except Exception as e:
            self.log(f"Erro na função sell: {e}", "red")

    # -------------------------
    # Estratégias
    # -------------------------
    # Breakout: compra quando rompe topo de N candles, vende se quebra fundo
    def strategy_breakout(self):
        closes = self.get_candles(limit=21)
        if len(closes) < 2:
            return
        current = closes[-1]
        previous = closes[:-1]
        top = max(previous)
        bottom = min(previous)

        if current > top:
            self.log("Breakout detectado → COMPRA", "cyan")
            self.buy(current)

        # se já tem operação e preço cai abaixo do bottom, força venda
        if current < bottom and self.operations:
            self.log("Quebra de fundo detectado → VENDA", "red")
            self.sell(0, current)

    # SMA: compra ao cruzar acima da SMA(long), vende ao cruzar abaixo
    def strategy_sma(self, period=30):
        closes = self.get_candles(limit=period + 1)
        if len(closes) < period + 1:
            return
        sma = sum(closes[:-1]) / Decimal(str(len(closes[:-1])))
        current = closes[-1]
        prev = closes[-2]

        # cruzamento simples: quando preço passa de abaixo para acima
        if prev <= sma and current > sma and self.usdt_balance >= Decimal("0.00000001"):
            self.log("Cruzamento SMA ↑ → COMPRA", "cyan")
            self.buy(current)

        # cruza de cima para baixo => vende
        if prev >= sma and current < sma and self.operations:
            self.log("Cruzamento SMA ↓ → VENDA", "red")
            self.sell(0, current)

    # RSI: compra abaixo de 30, vende acima de 70
    def calculate_rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains = Decimal("0")
        losses = Decimal("0")
        for i in range(1, period + 1):
            delta = Decimal(closes[-i]) - Decimal(closes[-i - 1])
            if delta > 0:
                gains += delta
            else:
                losses += abs(delta)
        avg_gain = gains / Decimal(period)
        avg_loss = losses / Decimal(period)
        if avg_loss == 0:
            return Decimal("100")
        rs = avg_gain / avg_loss
        rsi = Decimal("100") - (Decimal("100") / (Decimal("1") + rs))
        return rsi

    def strategy_rsi(self, period=14, buy_level=30, sell_level=70):
        closes = self.get_candles(limit=period + 5)
        if not closes:
            return
        rsi = self.calculate_rsi(closes, period=period)
        if rsi is None:
            return
        current = Decimal(closes[-1])
        self.log(f"RSI atual: {round(float(rsi), 2)}", "magenta")

        if rsi <= Decimal(str(buy_level)):
            self.log("RSI baixo → COMPRA", "cyan")
            self.buy(current)

        if rsi >= Decimal(str(sell_level)) and self.operations:
            self.log("RSI alto → VENDA", "red")
            self.sell(0, current)

    # DCA inteligente: compra adicional quando cair X% desde última compra, vende ao atingir lucro
    def strategy_dca(self):
        current_price = self.fetch_current_price()
        if current_price == 0:
            return

        if not self.operations:
            self.log("DCA: primeira compra", "cyan")
            self.buy(current_price)
            return

        # usa o preço da última operação para comparar
        last_price = Decimal(str(self.operations[-1]["price"]))
        # se caiu mais que drop_threshold (percentual), compra mais
        if current_price <= last_price * (Decimal("1") - self.drop_threshold):
            self.log(f"DCA: queda detectada ({float((last_price-current_price)/last_price)*100:.2f}%) → COMPRA", "yellow")
            self.buy(current_price)

        # se atingiu alvo de lucro (desde última compra) -> vende
        if current_price >= last_price * (Decimal("1") + self.profit_threshold) and self.operations:
            self.log("DCA: alvo de lucro atingido → VENDA", "green")
            self.sell(0, current_price)

    # Estrategia básica original (sell quando atinge target, buy em queda)
    def evaluate_operations(self):
        current_price = self.fetch_current_price()
        if current_price == 0:
            return

        for i, operation in enumerate(list(self.operations)):  # copia para iterar com segurança
            target = Decimal(str(operation.get("target", operation.get("price"))))
            amount = Decimal(str(operation["amount"]))

            # Vende se atingir target
            if current_price >= target:
                self.log("Meta atingida → VENDA", "green")
                self.sell(i, current_price)
                break

            # Compra adicional se cair muito (estratégia de escala)
            buy_price = Decimal(str(operation["price"]))
            if current_price <= buy_price * (Decimal("1") - self.drop_threshold):
                self.log("Queda significativa detectada → COMPRA adicional", "yellow")
                self.buy(current_price)
                break

    # Router de estratégias
    def run_strategy(self):
        s = self.strategy
        if s == "basic":
            self.evaluate_operations()
        elif s == "breakout":
            self.strategy_breakout()
        elif s == "sma":
            self.strategy_sma()
        elif s == "rsi":
            self.strategy_rsi()
        elif s == "dca":
            self.strategy_dca()
        else:
            self.log(f"Estratégia desconhecida '{s}', usando basic.", "red")
            self.evaluate_operations()

    # -------------------------
    # Inicialização e loop
    # -------------------------
    def initialize_operations(self):
        current = self.fetch_current_price()
        if current > 0:
            self.log("Inicializando primeira operação...", "cyan")
            self.buy(current)

    def run(self):
        try:
            self.log(f"Bot iniciado. Pair: {self.trade_pair} | Strategy: {self.strategy} | Simulado: {self.simulated}", "cyan")
            while True:
                now = datetime.now()

                # Janela para reconexão/limpeza (exemplo: 22:30 até 00:59)
                # Ajuste conforme necessidade
                # if (now.hour == 22 and now.minute >= 30) or (now.hour in (23, 0)):
                #     self.log("Janela de reinicialização/pausa programada (22:30-00:59).", "red")
                #     time.sleep(60)
                #     continue

                self.usdt_balance, self.btc_balance = self.fetch_balances()

                self.run_strategy()

                self.log(f"Saldo {self.fiat}: {round(float(self.usdt_balance), 2)}", "blue")
                self.log(f"Saldo {self.crypto}: {round(float(self.btc_balance), 6)}", "yellow")
                self.log(f"Operações na fila: {len(self.operations)}", "cyan")

                # Se fila vazia, garante que haja uma compra inicial em algumas estratégias
                if len(self.operations) == 0 and self.strategy in ("basic", "dca"):
                    self.buy(self.fetch_current_price())

                time.sleep(int(os.getenv("LOOP_SLEEP", "10")))
        except KeyboardInterrupt:
            self.log("Bot parado pelo usuário (KeyboardInterrupt).", "red")
        except Exception as e:
            self.log(f"Erro inesperado no run loop: {e}", "red")

# -------------------------
# Execução
# -------------------------
if __name__ == "__main__":
    API_KEY = os.getenv("API_KEY", "")
    API_SECRET = os.getenv("API_SECRET", "")

    if not API_KEY or not API_SECRET:
        print("API_KEY e API_SECRET não foram encontradas no .env. Rodando em modo SIMULADO por padrão.")
    bot = CryptoTradeBot(api_key=API_KEY, api_secret=API_SECRET)
    bot.run()
