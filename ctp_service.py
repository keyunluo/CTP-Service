# -*- coding: utf-8 -*-

import json, datetime, time, logging, os, threading, re, aiohttp
from sanic import Sanic, Blueprint, response
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from collections import defaultdict
import ctpwrapper as CTP
import ctpwrapper.ApiStructure as CTPStruct

api = Blueprint('trade_ctp', url_prefix='/trade/ctp')

# 通用工具
@api.listener('before_server_start')
async def before_server_start(app, loop):
    '''全局共享session'''
    global session, MAX_TIMEOUT, DATA_DIR, FILTER, logger, ctp_client, scheduler, base_url
    jar = aiohttp.CookieJar(unsafe=True)
    session = aiohttp.ClientSession(cookie_jar=jar, connector=aiohttp.TCPConnector(ssl=False))
    base_url = 'http://127.0.0.1:7000/trade/ctp'

    MAX_TIMEOUT = 10
    DATA_DIR = "ctp_client_data/"

    logger = logging.getLogger()
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
            '%(asctime)s %(name)-12s %(levelname)-8s %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    FILTER = lambda x: None if x > 1.797e+308 else x
    
    json_file = open("config.json")
    config = json.load(json_file)
    json_file.close()

    user_id = config["investor_id"]
    broker_id = config["broker_id"]
    password = config["password"]
    td_front = config["trader_server"]
    md_front = config["md_server"]
    app_id = config["app_id"]
    auth_code = config["auth_code"]
    
    ctp_client = Client(md_front, td_front, broker_id, app_id, auth_code, user_id, password)

    scheduler = AsyncIOScheduler()
 
    now = datetime.datetime.now()
    scheduler.add_job(login_request, 'cron', id='job_login', day_of_week='mon,tue,wed,thu,fri', hour='8,20', minute=40, second=0)
    scheduler.add_job(logout_request, 'cron', id='job_logout', day_of_week='mon,tue,wed,thu,fri,sat', hour='15,2', minute=40, second=0)

    if (now.strftime("%H:%M") > '08:40' and now.strftime("%H:%M") < '14:55') or (now.strftime("%H:%M") > '20:40' or now.strftime("%H:%M") < '02:25') and now.weekday() < 6:
        scheduler.add_job(login_request, trigger='date', next_run_time=datetime.datetime.now() + datetime.timedelta(seconds=10), id="pad_task")
    scheduler.start()

async def login_request():
    return await get_json(base_url + '/login')

async def logout_request():
    return await get_json(base_url + '/logout')
    
@api.listener('after_server_stop')
async def after_server_stop(app, loop):
    '''关闭session'''
    ctp_client.logout()
    await session.close()
    scheduler.shutdown()

async def get_json(url, headers={}):
    '''
    get请求json方法
    '''
    async with session.get(url, headers=headers) as resp:
        resp_json = await resp.json()
        return resp_json

class SpiHelper:
    def __init__(self):
        self._event = threading.Event()
        self._error = None

    def resetCompletion(self):
        self._event.clear()
        self._error = None

    def waitCompletion(self, operation_name = ""):
        if not self._event.wait(MAX_TIMEOUT):
            raise TimeoutError("%s超时" % operation_name)
        if self._error:
            raise RuntimeError(self._error)

    def notifyCompletion(self, error = None):
        self._error = error
        self._event.set()

    def _cvtApiRetToError(self, ret):
        assert(-3 <= ret <= -1)
        return ("网络连接失败", "未处理请求超过许可数", "每秒发送请求数超过许可数")[-ret - 1]

    def checkApiReturn(self, ret):
        if ret != 0:
            raise RuntimeError(self._cvtApiRetToError(ret))

    def checkApiReturnInCallback(self, ret):
        if ret != 0:
            self.notifyCompletion(self._cvtApiRetToError(ret))

    def checkRspInfoInCallback(self, info):
        if not info or info.ErrorID == 0:
            return True
        self.notifyCompletion(info.ErrorMsg)
        return False

class QuoteImpl(SpiHelper, CTP.MdApiPy):
    def __init__(self, front):
        SpiHelper.__init__(self)
        CTP.MdApiPy.__init__(self)
        self._receiver = None
        flow_dir = DATA_DIR + "md_flow/"
        os.makedirs(flow_dir, exist_ok = True)
        self.Create(flow_dir)
        self.RegisterFront(front)
        self.Init()
        self.waitCompletion("登录行情会话")
    
    def OnRspError(self, pRspInfo, nRequestID, bIsLast):
        print("OnRspError:")
        print("requestID:", nRequestID)
        print(pRspInfo)
        print(bIsLast)

    def __del__(self):
        self.Release()
        logger.info("已登出行情服务器...")

    def shutdown(self):
        self.Release()
        logger.info("已登出行情服务器...")

    def OnFrontConnected(self):
        logger.info("已连接行情服务器...")
        field = CTPStruct.ReqUserLoginField()
        self.checkApiReturnInCallback(self.ReqUserLogin(field, 0))
        self.status = 0
        
    def OnFrontDisconnected(self, nReason):
        logger.info("已断开行情服务器:{}...".format(nReason))
        print("Md OnFrontDisconnected {0}".format(nReason))
    
    def OnHeartBeatWarning(self, nTimeLapse):
        """心跳超时警告。当长时间未收到报文时，该方法被调用。
        @param nTimeLapse 距离上次接收报文的时间
        """
        logger.info('Md OnHeartBeatWarning, time = {0}'.format(nTimeLapse))

    def OnRspUserLogin(self, _, info, req_id, is_last):
        assert(req_id == 0)
        assert(is_last)
        if not self.checkRspInfoInCallback(info):
            return
        logger.info("已登录行情会话...")
        self.status = 1
        self.notifyCompletion()

    def setReceiver(self, func):
        old_func = self._receiver
        self._receiver = func
        return old_func

    def subscribe(self, codes):
        self.resetCompletion()
        self.checkApiReturn(self.SubscribeMarketData(codes))
        self.waitCompletion("订阅行情")

    def OnRspSubMarketData(self, field, info, _, is_last):
        if not self.checkRspInfoInCallback(info):
            assert(is_last)
            return
        logger.info("已订阅<%s>的行情..." % field.InstrumentID)
        if is_last:
            self.notifyCompletion()

    def OnRtnDepthMarketData(self, field):
        if not self._receiver:
            return
        self._receiver({"trade_time": field.TradingDay[:4] + '-' + field.TradingDay[4:6] + '-' + field.TradingDay[6:] + " " + field.UpdateTime, "update_sec": int(field.UpdateMillisec), 
                "code": field.InstrumentID, "price": FILTER(field.LastPrice),
                "open": FILTER(field.OpenPrice), "close": FILTER(field.ClosePrice),
                "highest": FILTER(field.HighestPrice), "lowest": FILTER(field.LowestPrice),
                "upper_limit": FILTER(field.UpperLimitPrice),
                "lower_limit": FILTER(field.LowerLimitPrice),
                "settlement": FILTER(field.SettlementPrice), "volume": field.Volume,
                "turnover": field.Turnover, "open_interest": int(field.OpenInterest),
                "pre_close": FILTER(field.PreClosePrice),
                "pre_settlement": FILTER(field.PreSettlementPrice),
                "pre_open_interest": int(field.PreOpenInterest),
                "ask1": (FILTER(field.AskPrice1), field.AskVolume1),
                "bid1": (FILTER(field.BidPrice1), field.BidVolume1),
                "ask2": (FILTER(field.AskPrice2), field.AskVolume2),
                "bid2": (FILTER(field.BidPrice2), field.BidVolume2),
                "ask3": (FILTER(field.AskPrice3), field.AskVolume3),
                "bid3": (FILTER(field.BidPrice3), field.BidVolume3),
                "ask4": (FILTER(field.AskPrice4), field.AskVolume4),
                "bid4": (FILTER(field.BidPrice4), field.BidVolume4),
                "ask5": (FILTER(field.AskPrice5), field.AskVolume5),
                "bid5": (FILTER(field.BidPrice5), field.BidVolume5)})

    def unsubscribe(self, codes):
        self.resetCompletion()
        self.checkApiReturn(self.UnSubscribeMarketData(codes))
        self.waitCompletion("取消订阅行情")

    def OnRspUnSubMarketData(self, field, info, _, is_last):
        if not self.checkRspInfoInCallback(info):
            assert(is_last)
            return
        logger.info("已取消订阅<%s>的行情..." % field.InstrumentID)
        if is_last:
            self.notifyCompletion()

class TraderImpl(SpiHelper, CTP.TraderApiPy):
    def __init__(self, front, broker_id, app_id, auth_code, user_id, password):
        SpiHelper.__init__(self)
        CTP.TraderApiPy.__init__(self)
        self._last_query_time = 0
        self._broker_id = broker_id
        self._app_id = app_id
        self._auth_code = auth_code
        self._user_id = user_id
        self._password = password
        self._front_id = None
        self._session_id = None
        self._order_action = None
        self._order_ref = 0
        flow_dir = DATA_DIR + "td_flow/"
        os.makedirs(flow_dir, exist_ok = True)
        self.Create(flow_dir)
        self.RegisterFront(front)
        self.SubscribePrivateTopic(2)   #THOST_TERT_QUICK
        self.SubscribePublicTopic(2)    #THOST_TERT_QUICK
        self.Init()
        self.waitCompletion("登录交易会话")
        del self._app_id, self._auth_code, self._password
        self._getInstruments()
        self.instruments_option = defaultdict(list)
        self.instruments_future = defaultdict(list)
        self._buildInstrumentsDict()

    def _limitFrequency(self):
        delta = time.time() - self._last_query_time
        if delta < 1:
            time.sleep(1 - delta)
        self._last_query_time = time.time()

    def __del__(self):
        self.Release()
        logger.info("已登出交易服务器...")
    
    def shutdown(self):
        self.Release()
        logger.info("已登出交易服务器...")

    def OnFrontConnected(self):
        logger.info("已连接交易服务器...")
        field = CTPStruct.ReqAuthenticateField(BrokerID = self._broker_id,
                AppID = self._app_id, AuthCode = self._auth_code, UserID = self._user_id)
        self.checkApiReturnInCallback(self.ReqAuthenticate(field, 0))
    
    def OnRspError(self, pRspInfo, nRequestID, bIsLast):
        print("OnRspError:")
        print("requestID:", nRequestID)
        print(pRspInfo)
        print(bIsLast)

    def OnHeartBeatWarning(self, nTimeLapse):
        """心跳超时警告。当长时间未收到报文时，该方法被调用。
        @param nTimeLapse 距离上次接收报文的时间
        """
        logger.info("OnHeartBeatWarning time: ", nTimeLapse)

    def OnFrontDisconnected(self, nReason):
        logger.info("已断开交易服务器:{}...".format(nReason))
        print("OnFrontDisConnected:", nReason)

    def OnRspAuthenticate(self, _, info, req_id, is_last):
        assert(req_id == 0)
        assert(is_last)
        if not self.checkRspInfoInCallback(info):
            return
        logger.info("已通过交易终端认证...")
        field = CTPStruct.ReqUserLoginField(BrokerID = self._broker_id,
                UserID = self._user_id, Password = self._password)
        self.checkApiReturnInCallback(self.ReqUserLogin(field, 1))

    def OnRspUserLogin(self, field, info, req_id, is_last):
        assert(req_id == 1)
        assert(is_last)
        if not self.checkRspInfoInCallback(info):
            return
        self._front_id = field.FrontID
        self._session_id = field.SessionID
        logger.info("已登录交易会话...")
        field = CTPStruct.SettlementInfoConfirmField(BrokerID = self._broker_id,
                InvestorID = self._user_id)
        self.checkApiReturnInCallback(self.ReqSettlementInfoConfirm(field, 2))

    def OnRspSettlementInfoConfirm(self, _, info, req_id, is_last):
        assert(req_id == 2)
        assert(is_last)
        if not self.checkRspInfoInCallback(info):
            return
        logger.info("已确认结算单...")
        self.notifyCompletion()

    def _getInstruments(self):
        file_path = DATA_DIR + "instruments.dat"
        now_date = time.strftime("%Y-%m-%d", time.localtime())
        if os.path.exists(file_path):
            fd = open(file_path)
            cached_date = fd.readline()
            if cached_date[: -1] == now_date:
                self._instruments = json.load(fd)
                fd.close()
                logger.info("已加载全部共%d个合约..." % len(self._instruments))
                return
            fd.close()
        self._instruments = {}
        self.resetCompletion()
        self._limitFrequency()
        self.checkApiReturn(self.ReqQryInstrument(CTPStruct.QryInstrumentField(), 3))
        last_count = 0
        while True:
            try:
                self.waitCompletion("获取所有合约")
                break
            except TimeoutError as e:
                count = len(self._instruments)
                if count == last_count:
                    raise e
                logger.info("已获取%d个合约..." % count)
                last_count = count
        fd = open(file_path, "w")
        fd.write(now_date + "\n")
        json.dump(self._instruments, fd, ensure_ascii=False)
        fd.close()
        logger.info("已保存全部共%d个合约..." % len(self._instruments))
    
    def _buildInstrumentsDict(self):
        for symbol in self._instruments:
            instrument = self._instruments[symbol]
            instrument["symbol"] = symbol
            if re.search(r"[\d\-][CP][\d\-]", symbol):
                try:
                    self.instruments_option[re.findall(r"([A-Za-z]{2,}\d{2,})", symbol)[0]].append(instrument)
                except:
                    self.instruments_option[re.findall(r'(^[A-Za-z]\d+)', symbol)[0]].append(instrument)
            else:
                self.instruments_future[instrument['exchange']].append(instrument)

    def OnRspQryInstrument(self, field, info, req_id, is_last):
        assert(req_id == 3)
        if not self.checkRspInfoInCallback(info):
            assert(is_last)
            return
        if field:
            if field.OptionsType == '1':        #THOST_FTDC_CP_CallOptions
                option_type = "call"
            elif field.OptionsType == '2':      #THOST_FTDC_CP_PutOptions
                option_type = "put"
            else:
                option_type = None
            expire_date = None if field.ExpireDate == "" else       \
                    time.strftime("%Y-%m-%d", time.strptime(field.ExpireDate, "%Y%m%d"))
            self._instruments[field.InstrumentID] = {"name": field.InstrumentName,
                    "exchange": field.ExchangeID, "multiple": field.VolumeMultiple,
                    "price_tick": field.PriceTick, "expire_date": expire_date,
                    "long_margin_ratio": FILTER(field.LongMarginRatio),
                    "short_margin_ratio": FILTER(field.ShortMarginRatio),
                    "option_type": option_type, "strike_price": FILTER(field.StrikePrice),
                    "is_trading": bool(field.IsTrading)}
        if is_last:
            logger.info("已获取全部共%d个合约..." % len(self._instruments))
            self.notifyCompletion()

    def getAccount(self):
        #THOST_FTDC_BZTP_Future = 1
        field = CTPStruct.QryTradingAccountField(BrokerID = self._broker_id,
                InvestorID = self._user_id, CurrencyID = "CNY", BizType = '1')
        self.resetCompletion()
        self._limitFrequency()
        self.checkApiReturn(self.ReqQryTradingAccount(field, 8))
        self.waitCompletion("获取资金账户")
        return self._account

    def OnRspQryTradingAccount(self, field, info, req_id, is_last):
        assert(req_id == 8)
        assert(is_last)
        if not self.checkRspInfoInCallback(info):
            return
        self._account = {"balance": field.Balance, "margin": field.CurrMargin,
                "available": field.Available}
        logger.info("已获取资金账户...")
        self.notifyCompletion()

    def getOrders(self):
        self._orders = {}
        field = CTPStruct.QryOrderField(BrokerID = self._broker_id,
                InvestorID = self._user_id)
        self.resetCompletion()
        self._limitFrequency()
        self.checkApiReturn(self.ReqQryOrder(field, 4))
        self.waitCompletion("获取所有报单")
        return self._orders

    def _gotOrder(self, order):
        if len(order.OrderSysID) == 0:
            return
        oid = "%s@%s" % (order.OrderSysID, order.InstrumentID)
        (direction, volume) = (int(order.Direction), order.VolumeTotalOriginal)
        assert(direction in (0, 1))
        if order.CombOffsetFlag == '1':     #THOST_FTDC_OFEN_Close
            direction = 1 - direction
            volume = -volume
        direction = "short" if direction else "long"
        #THOST_FTDC_OST_AllTraded = 0, THOST_FTDC_OST_Canceled = 5
        is_active = order.OrderStatus not in ('0', '5')
        assert(oid not in self._orders)
        self._orders[oid] = {"code": order.InstrumentID, "direction": direction,
                "price": order.LimitPrice, "volume": volume,
                "volume_traded": order.VolumeTraded, "is_active": is_active}

    def OnRspQryOrder(self, field, info, req_id, is_last):
        assert(req_id == 4)
        if not self.checkRspInfoInCallback(info):
            assert(is_last)
            return
        if field:
            self._gotOrder(field)
        if is_last:
            logger.info("已获取所有报单...")
            self.notifyCompletion()

    def getPositions(self):
        self._positions = []
        field = CTPStruct.QryInvestorPositionField(BrokerID = self._broker_id,
                InvestorID = self._user_id)
        self.resetCompletion()
        self._limitFrequency()
        self.checkApiReturn(self.ReqQryInvestorPosition(field, 5))
        self.waitCompletion("获取所有持仓")
        return self._positions

    def _gotPosition(self, position):
        code = position.InstrumentID
        if position.PosiDirection == '2':       #THOST_FTDC_PD_Long
            direction = "long"
        elif position.PosiDirection == '3':     #THOST_FTDC_PD_Short
            direction = "short"
        else:
            return
        volume = position.Position
        if volume == 0:
            return
        self._positions.append({"code": code, "direction": direction,
                    "volume": volume, "margin": position.UseMargin,
                    "cost": position.OpenCost})

    def OnRspQryInvestorPosition(self, field, info, req_id, is_last):
        assert(req_id == 5)
        if not self.checkRspInfoInCallback(info):
            assert(is_last)
            return
        if field:
            self._gotPosition(field)
        if is_last:
            logger.info("已获取所有持仓...")
            self.notifyCompletion()

    def OnRtnOrder(self, order):
        if self._order_action:
            if self._order_action(order):
                self._order_action = None

    def _handleNewOrder(self, order):
        order_ref = None if len(order.OrderRef) == 0 else int(order.OrderRef)
        if (order.FrontID, order.SessionID, order_ref) !=               \
                (self._front_id, self._session_id, self._order_ref):
            return False
        logging.debug(order)
        if order.OrderStatus == 'a':                #THOST_FTDC_OST_Unknown
            return False
        if order.OrderSubmitStatus == '4':          #THOST_FTDC_OSS_InsertRejected
            self.notifyCompletion(order.StatusMsg)
            return True
        if order.TimeCondition == '1':              #THOST_FTDC_TC_IOC
            #THOST_FTDC_OST_AllTraded = 0, THOST_FTDC_OST_Canceled = 5
            if order.OrderStatus in ('0', '5'):
                logger.info("已执行IOC单，成交量：%d" % order.VolumeTraded)
                self._traded_volume = order.VolumeTraded
                self.notifyCompletion()
                return True
        else:
            assert(order.TimeCondition == '3')      #THOST_FTDC_TC_GFD
            if order.OrderSubmitStatus == '3':      #THOST_FTDC_OSS_Accepted
                #THOST_FTDC_OST_AllTraded = 0, THOST_FTDC_OST_PartTradedQueueing = 1
                #THOST_FTDC_OST_PartTradedNotQueueing = 2, THOST_FTDC_OST_NoTradeQueueing = 3
                #THOST_FTDC_OST_NoTradeNotQueueing = 4, THOST_FTDC_OST_Canceled = 5
                assert(order.OrderStatus in ('0', '1', '2', '3', '4', '5'))
                assert(len(order.OrderSysID) != 0)
                self._order_id = "%s@%s" % (order.OrderSysID, order.InstrumentID)
                logger.info("已提交限价单（单号：<%s>）" % self._order_id)
                self.notifyCompletion()
                return True
        return False

    def _order(self, code, direction, volume, price, min_volume):
        if code not in self._instruments:
            raise ValueError("合约<%s>不存在！" % code)
        exchange = self._instruments[code]["exchange"]
        if direction == "long":
            direction = 0               #THOST_FTDC_D_Buy
        elif direction == "short":
            direction = 1               #THOST_FTDC_D_Sell
        else:
            raise ValueError("错误的买卖方向<%s>" % direction)
        if volume != int(volume) or volume == 0:
            raise ValueError("交易数量<%s>必须是非零整数" % volume)
        if volume > 0:
            offset_flag = '0'           #THOST_FTDC_OF_Open
        else:
            offset_flag = '1'           #THOST_FTDC_OF_Close
            volume = -volume
            direction = 1 - direction
        direction = str(direction)
        #Market Price Order
        if price == 0:
            if exchange == "CFFEX":
                price_type = 'G'        #THOST_FTDC_OPT_FiveLevelPrice
            else:
                price_type = '1'        #THOST_FTDC_OPT_AnyPrice
            #THOST_FTDC_TC_IOC, THOST_FTDC_VC_AV
            (time_cond, volume_cond) = ('1', '1')
        #Limit Price Order
        elif min_volume == 0:
            #THOST_FTDC_OPT_LimitPrice, THOST_FTDC_TC_GFD, THOST_FTDC_VC_AV
            (price_type, time_cond, volume_cond) = ('2', '3', '1')
        #FAK Order
        else:
            min_volume = abs(min_volume)
            if min_volume > volume:
                raise ValueError("最小成交量<%s>不能超过交易数量<%s>" % (min_volume, volume))
            #THOST_FTDC_OPT_LimitPrice, THOST_FTDC_TC_IOC, THOST_FTDC_VC_MV
            (price_type, time_cond, volume_cond) = ('2', '1', '2')
        self._order_ref += 1
        self._order_action = self._handleNewOrder
        field = CTPStruct.InputOrderField(BrokerID = self._broker_id,
                InvestorID = self._user_id, ExchangeID = exchange, InstrumentID = code,
                Direction = direction, CombOffsetFlag = offset_flag,
                TimeCondition = time_cond, VolumeCondition = volume_cond,
                OrderPriceType = price_type, LimitPrice = price,
                VolumeTotalOriginal = volume, MinVolume = min_volume,
                CombHedgeFlag = '1',            #THOST_FTDC_HF_Speculation
                ContingentCondition = '1',      #THOST_FTDC_CC_Immediately
                ForceCloseReason = '0',         #THOST_FTDC_FCC_NotForceClose
                OrderRef = "%12d" % self._order_ref)
        self.resetCompletion()
        self.checkApiReturn(self.ReqOrderInsert(field, 6))
        self.waitCompletion("录入报单")

    def OnRspOrderInsert(self, field, info, req_id, is_last):
        assert(req_id == 6)
        assert(is_last)
        self.OnErrRtnOrderInsert(field, info)

    def OnErrRtnOrderInsert(self, _, info):
        success = self.checkRspInfoInCallback(info)
        assert(not success)

    def orderMarket(self, code, direction, volume):
        self._order(code, direction, volume, 0, 0)
        return self._traded_volume

    def orderFAK(self, code, direction, volume, price, min_volume):
        assert(price > 0)
        self._order(code, direction, volume, price, 1 if min_volume == 0 else min_volume)
        return self._traded_volume

    def orderFOK(self, code, direction, volume, price):
        return self.orderFAK(code, direction, volume, price, volume)

    def orderLimit(self, code, direction, volume, price):
        assert(price > 0)
        self._order(code, direction, volume, price, 0)
        return self._order_id

    def _handleDeleteOrder(self, order):
        oid = "%s@%s" % (order.OrderSysID, order.InstrumentID)
        if oid != self._order_id:
            return False
        logging.debug(order)
        if order.OrderSubmitStatus == '5':      #THOST_FTDC_OSS_CancelRejected
            self.notifyCompletion(order.StatusMsg)
            return True
        #THOST_FTDC_OST_AllTraded = 0, THOST_FTDC_OST_Canceled = 5
        if order.OrderStatus in ('0', '5'):
            logger.info("已撤销限价单，单号：<%s>" % self._order_id)
            self.notifyCompletion()
            return True
        return False

    def deleteOrder(self, order_id):
        items = order_id.split("@")
        if len(items) != 2:
            raise ValueError("订单号<%s>格式错误" % order_id)
        (sys_id, code) = items
        if code not in self._instruments:
            raise ValueError("订单号<%s>中的合约号<%s>不存在" % (order_id, code))
        field = CTPStruct.InputOrderActionField(BrokerID = self._broker_id,
                InvestorID = self._user_id, UserID = self._user_id,
                ActionFlag = '0',               #THOST_FTDC_AF_Delete
                ExchangeID = self._instruments[code]["exchange"],
                InstrumentID = code, OrderSysID = sys_id)
        self.resetCompletion()
        self._order_id = order_id
        self._order_action = self._handleDeleteOrder
        self.checkApiReturn(self.ReqOrderAction(field, 7))
        self.waitCompletion("撤销报单")

    def OnRspOrderAction(self, field, info, req_id, is_last):
        assert(req_id == 7)
        assert(is_last)
        self.OnErrRtnOrderAction(field, info)

    def OnErrRtnOrderAction(self, _, info):
        success = self.checkRspInfoInCallback(info)
        assert(not success)

class Client:
    def __init__(self, md_front, td_front, broker_id, app_id, auth_code, user_id, password):
        self._md = None
        self._td = None
        self.md_front = md_front
        self.td_front = td_front
        self.broker_id = broker_id
        self.app_id = app_id
        self.auth_code = auth_code
        self.user_id = user_id
        self.password = password
    
    def login(self):
        '''
        登录行情、交易
        '''
        self._td = TraderImpl(self.td_front, self.broker_id, self.app_id, self.auth_code, self.user_id, self.password)
        self._md = QuoteImpl(self.md_front)
    
    def logout(self):
        '''
        登出
        '''
        self._md.shutdown()
        self._td.shutdown()
    
    def setReceiver(self):
        '''
        tick行情处理函数
        '''
        try:
            from hq_func import parse_hq
        except:
            parse_hq = lambda x: print(x)
        return self._md.setReceiver(parse_hq)

    def subscribe(self, codes):
        '''
        订阅合约代码
        '''
        for code in codes:
            if code not in self._td._instruments:
                raise ValueError("合约<%s>不存在" % code)
        self._md.subscribe(codes)

    def get_instruments_option(self, future=None):
        '''
        获取期权合约列表，可指定对应的期货代码
        '''
        if future is None:
            return self._td.instruments_option
        return self._td.instruments_option.get(future, None)

    def get_instruments_future(self, exchange=None):
        '''
        获取期货合约列表，可指定对应的交易所
        '''
        if exchange is None:
            return self._td.instruments_future
        return self._td.instruments_future[exchange]

    def unsubscribe(self, codes):
        '''
        取消订阅
        '''
        self._md.unsubscribe(codes)

    def getInstrument(self, code):
        '''
        获取指定合约详情
        '''
        if code not in self._td._instruments:
            raise ValueError("合约<%s>不存在" % code)
        return self._td._instruments[code].copy()

    def getAccount(self):
        '''
        获取账号资金情况
        '''
        return self._td.getAccount()

    def getOrders(self):
        '''
        获取当天订单
        '''
        return self._td.getOrders()

    def getPositions(self):
        '''
        获取持仓
        '''
        return self._td.getPositions()

    def orderMarket(self, code, direction, volume):
        '''
        市价下单
        '''
        return self._td.orderMarket(code, direction, volume)

    def orderFAK(self, code, direction, volume, price, min_volume):
        '''
        FAK下单
        '''
        return self._td.orderFAK(code, direction, volume, price, min_volume)

    def orderFOK(self, code, direction, volume, price):
        '''
        FOK下单
        '''
        return self._td.orderFOK(code, direction, volume, price)

    def orderLimit(self, code, direction, volume, price):
        '''
        限价单
        '''
        return self._td.orderLimit(code, direction, volume, price)

    def deleteOrder(self, order_id):
        '''
        撤销订单
        '''
        self._td.deleteOrder(order_id)

@api.route('/login', methods=['GET'])    
async def login(request):
    ctp_client.login()
    return response.json({"time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')})

@api.route('/logout', methods=['GET'])    
async def logout(request):
    ctp_client.logout()
    return response.json({"time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')})

@api.route('/get_account', methods=['GET'])    
async def get_account(request):
    try:
        data = ctp_client.getAccount()
        return response.json(data, ensure_ascii=False)
    except Exception as e:
        return response.json({"error": str(e)}, ensure_ascii=False)

@api.route('/get_postion', methods=['GET'])    
async def get_postion(request):
    try:
        data = ctp_client.getPositions()
        return response.json(data, ensure_ascii=False)
    except Exception as e:
        return response.json({"error": str(e)}, ensure_ascii=False)

@api.route('/order_limit', methods=['GET'])    
async def order_limit(request):
    '''
    code为合约代码，direction为字符串"long"或者"short"之一，表示多头或空头。volume为整数，表示交易数量，正数表示该方向加仓，负数表示该方向减仓。price为float类型的价格。提交成功返回“订单号@合约号”。
    '''
    code = request.args.get("code")
    direction = request.args.get("direction", "long")
    volume = int(request.args.get("volume", 1))
    price = float(request.args.get("price", "0"))

    try:
        data = ctp_client.orderLimit(code, direction, volume, price)
        return response.json(data, ensure_ascii=False)
    except Exception as e:
        return response.json({"error": str(e)}, ensure_ascii=False)

@api.route('/order_market', methods=['GET'])    
async def order_market(request):
    '''
    市价单不指定价格，而是以当前市场价格成交，能成交多少就成交多少，剩余未成交的撤单。返回成交数量，介于[0, volume]之间。
    '''
    code = request.args.get("code")
    direction = request.args.get("direction", "long")
    volume = int(request.args.get("volume", 1))

    try:
        data = ctp_client.orderMarket(code, direction, volume)
        return response.json(data, ensure_ascii=False)
    except Exception as e:
        return response.json({"error": str(e)}, ensure_ascii=False)

@api.route('/order_delete', methods=['GET'])    
async def order_delete(request):
    '''
    已提交未完全成交的限价单可以撤单。order_id为orderLimit()的返回值。
    '''
    order_id = request.args.get("order_id")
    try:
        data = ctp_client.deleteOrder(order_id)
        return response.json(data, ensure_ascii=False)
    except Exception as e:
        return response.json({"error": str(e)}, ensure_ascii=False)

@api.route('/get_orders', methods=['GET'])    
async def get_orders(request):
    try:
        data = ctp_client.getPositions()
        return response.json(data, ensure_ascii=False)
    except Exception as e:
        return response.json({"error": str(e)}, ensure_ascii=False)

@api.route('/get_instruments_future', methods=['GET'])    
async def get_instruments_future(request):
    exchange = request.args.get("exchange", "")
    try:
        if exchange == "":
            data = ctp_client.get_instruments_future()
        else:
            data = ctp_client.get_instruments_future(exchange)
        return response.json(data, ensure_ascii=False)
    except Exception as e:
        return response.json({"error": str(e)}, ensure_ascii=False)

@api.route('/get_instruments_option', methods=['GET'])    
async def get_instruments_option(request):
    future = request.args.get("future", "")
    try:
        if future == "":
            data = ctp_client.get_instruments_option()
        else:
            data = ctp_client.get_instruments_option(future)
        return response.json(data, ensure_ascii=False)
    except Exception as e:
        return response.json({"error": str(e)}, ensure_ascii=False)

@api.route('/get_instruments_detail', methods=['GET'])    
async def get_instruments_detail(request):
    code = request.args.get("code", "")
    try:
        if code != "":
            data = ctp_client.getInstrument()
        else:
            data = {}
        return response.json(data, ensure_ascii=False)
    except Exception as e:
        return response.json({"error": str(e)}, ensure_ascii=False)


@api.route('/subscribe', methods=['GET'])    
async def subscribe(request):
    codes = request.args.get("codes")
    try:
        if codes != "":
            data = ctp_client.subscribe(codes.split(','))
            ctp_client.setReceiver()
        else:
            data = {}
        return response.json(data, ensure_ascii=False)
    except Exception as e:
        return response.json({"error": str(e)}, ensure_ascii=False)

@api.route('/unsubscribe', methods=['GET'])    
async def unsubscribe(request):
    codes = request.args.get("codes")
    try:
        if codes != "":
            data = ctp_client.unsubscribe(codes.split(','))
        else:
            data = {}
        return response.json(data, ensure_ascii=False)
    except Exception as e:
        return response.json({"error": str(e)}, ensure_ascii=False)

@api.route('/market/event', methods=['GET'])
async def market_event(request):
    '''
    事件数据
    '''
    event_date = request.args.get("event_date", "")

    if event_date == '':
        event_date = datetime.date.today()
    else:
        event_date = datetime.date.fromisoformat(event_date)

    url = 'https://cdn-rili.jin10.com/web_data/{}/daily/{}/{}/economics.json'.format(event_date.year, event_date.month, event_date.day)
    headers = {'x-app-id': 'bVBF4FyRTn5NJF5n', 'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36', 'x-version': '1.0.0', 'accept': 'application/json, text/plain, */*', 'referer': 'https://rili.jin10.com/', 'authority': 'cdn-rili.jin10.com'}
    
    data = await get_json(url, headers=headers)

    return response.json(data, ensure_ascii=False)

@api.route('/market/news', methods=['GET'])
async def market_news(request):
    '''
    新闻数据
    '''
    max_date = request.args.get("max_date", "")
    if max_date == "":
        max_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    url = 'https://flash-api.jin10.com/get_flash_list?channel=-8200&max_time={}&vip=1'.format(max_date)
    headers = {'x-app-id': 'bVBF4FyRTn5NJF5n', 'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36', 'x-version': '1.0.0', 'accept': 'application/json, text/plain, */*', 'referer': 'https://www.jin10.com/', 'authority': 'flash-api.jin10.com'}
    
    data = await get_json(url, headers=headers)
    return response.json(data, ensure_ascii=False)


@api.route('/market/realtime_hq', methods=['GET'])
async def market_realtime_hq(request):
    '''
    行情数据：实时
    '''
    code = request.args.get('code', 'CNH')
    url = 'https://centerapi.fx168api.com/app/api/QuoteOrder/GetQuoteInfoList?quoteCode={}&showArea=1'.format(code)
    headers = {'authority': 'centerapi.fx168api.com', 'accept': 'application/json, text/plain, */*', 'referer': 'https://www.fx168news.com/', 'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36'}

    data = await get_json(url, headers=headers)

    return response.json(data, ensure_ascii=False)

@api.route('/market/realtime_snap', methods=['GET'])
async def market_realtime_snap(request):
    '''
    行情数据：快照
    '''
    dtype = request.args.get('dtype', '金属钢材')
    category = {"金属钢材": "003002", "能源化工": "003003", "农产品": "003004", "中金所": "011001005", "上期所": "011001001", "上期能源": "011001002", "大商所": "011001003", "郑商所": "011001004", "纽约NYMEX": "011002001", "纽约COMEX": "011002002", "芝加哥CBOT": "011002003", "芝加哥CME": "011002004", "芝加哥CBOE": "011002005", "伦敦LME": "011002006", "洲际ICE": "011002007", "东京TOCM": "011002008", "香港HKEX": "011002009", "股指": "007001", "外汇": "002001", "加密货币": "008", "债券": "009"}.get(dtype)
    url = 'https://centerapi.fx168api.com/app/api/QuoteOrder/GetQuoteInfoByCategoryCode?categoryCode={}&pageNo=1&pageSize=200&showArea=1'.format(category)
    headers = {'authority': 'centerapi.fx168api.com', 'accept': 'application/json, text/plain, */*', 'referer': 'https://www.fx168news.com/', 'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36'}

    data = await get_json(url, headers=headers)

    return response.json(data, ensure_ascii=False)

@api.route('/market/realtime_dayline', methods=['GET'])
async def market_realtime_dayline(request):
    '''
    行情数据: 日线
    '''
    code = request.args.get('code', 'MTWTI0')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    if start_date == '':
        start_date = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    if end_date == '':
        end_date = datetime.date.today().strftime("%Y-%m-%d")
    url = 'https://centerapi.fx168api.com/app/api/TradingInterface/history?symbol={}&resolution=D&from={}&to={}&firstDataRequest=false'.format(code, int(1000 * datetime.datetime.fromisoformat(start_date).timestamp()), int(1000 * datetime.datetime.fromisoformat(end_date).timestamp()))
    headers = {'authority': 'centerapi.fx168api.com', 'accept': 'application/json, text/plain, */*', 'referer': 'https://www.fx168news.com/', 'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36'}

    data = await get_json(url, headers=headers)

    return response.json(data, ensure_ascii=False)



if __name__ == '__main__':
    app = Sanic(name=__name__)
    app.config.RESPONSE_TIMEOUT = 6000000
    app.config.REQUEST_TIMEOUT = 6000000
    app.config.KEEP_ALIVE_TIMEOUT = 600000
    app.blueprint(api)
    app.run(host='0.0.0.0', port=7000, workers=1, debug=True, auto_reload=True)