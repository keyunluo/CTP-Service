# CTP接口Python Web服务

基于[ctpwrapper](https://github.com/nooperpudd/ctpwrapper)和[ctp_client](https://github.com/zhou-yuxin/ctp_client)的CTP轻量级Python Web服务封装，可查询期货、期权行情以及进行下单交易。

- [x] HTTP请求Tick行情
- [x] HTTP请求期货期权合约代码
- [x] 内外盘期货实时行情
- [x] 新闻资讯、经济数据
- [x] HTTP下单(限价、市价单的开仓与平仓)
- [x] HTTP获取持仓以及资金
- [x] 每日定时自动启动(白天8:40，夜盘20:40)

## 安装

- 首先安装cython等: `pip install -U cython aiohttp apscheduler`
- 然后安装ctpwrapper: `pip install -U ctpwrapper`
- 最后安装web服务器: `pip install sanic<22`

## 使用

### 配置config.json

配置CTP实盘账号或Simnow账号
```json
{
    "investor_id": "******",
    "broker_id": "9999",
    "password": "******",
    "md_server": "tcp://180.168.146.187:10131",
    "trader_server": "tcp://180.168.146.187:10130",
    "app_id": "simnow_client_test",
    "auth_code": "0000000000000000"
  }
```

### 启动程序

```shell
python ctp_service.py
```

## HTTP接口

### 行情功能

- 获取期货合约所有代码
  
```python
data = requests.get('http://127.0.0.1:7000/trade/ctp/get_instruments_future?exchange=INE').json()
print(data[0])
{'name': '原油2309', 'exchange': 'INE', 'multiple': 1000, 'price_tick': 0.1, 'expire_date': '2023-08-31', 'long_margin_ratio': 0.17, 'short_margin_ratio': 0.17, 'option_type': None, 'strike_price': 0.0, 'is_trading': True, 'symbol': 'sc2309'}
```

- 获取期权合约所有代码
  
```python
data = requests.get('http://127.0.0.1:7000/trade/ctp/get_instruments_option?func_name=sc2302').json()
print(data[0])
{'name': 'sc2302C510', 'exchange': 'INE', 'multiple': 1000, 'price_tick': 0.05, 'expire_date': '2022-12-28', 'long_margin_ratio': None, 'short_margin_ratio': None, 'option_type': 'call', 'strike_price': 510.0, 'is_trading': True, 'symbol': 'sc2302C510'}
```

- 设置订阅tick行情处理函数
  
在`hq_func.py`文件中定义自己的`parse_hq`函数，示例仅将行情打印出来

- 订阅、取消订阅行情
  
```python
data = requests.get('http://127.0.0.1:7000/trade/ctp/subscribe?codes=MA301').json()
print(data)
{'trade_time': '2022-11-24 03:07:28', 'update_sec': 0, 'code': 'MA301', 'price': 2583.0, 'open': 2530.0, 'close': 2553.0, 'highest': 2588.0, 'lowest': 2523.0, 'upper_limit': 2732.0, 'lower_limit': 2374.0, 'settlement': 2546.0, 'volume': 1690398, 'turnover': 4325728482.0, 'open_interest': 1013956, 'pre_close': 2542.0, 'pre_settlement': 2553.0, 'pre_open_interest': 1068566, 'ask1': (2584.0, 659), 'bid1': (2583.0, 497), 'ask2': (None, 0), 'bid2': (None, 0), 'ask3': (None, 0), 'bid3': (None, 0), 'ask4': (None, 0), 'bid4': (None, 0), 'ask5': (None, 0), 'bid5': (None, 0)}

data = requests.get('http://127.0.0.1:7000/trade/ctp/unsubscribe?codes=MA301').json()
```

- 查询新闻
  
```python
data = requests.get('http://127.0.0.1:7000/trade/ctp/market/news').json()
print(data['data'][:1])
[{'id': '20221126112645980100',
  'time': '2022-11-26 11:26:45',
  'type': 0,
  'data': {'pic': '',
   'content': '<b>【金十期货整理：央行降准对大宗商品期货市场有何影响？】</b><br/><span class="section-news">1. 光大期货 宏观分析师于洁：历次降准对黑色系商品影响更大，主要因为黑色品种的需求完全看国内情况，其中螺纹钢基本上在降准之后都表现上涨。</span><br/><span class="section-news">2. 申银万国期货研究所所长助理汪洋：接下来大宗商品走势仍要关注美联储加息和国内经济复苏情况。美联储12月份议息会议临近，预计美国通胀稳步下行有助于市场稳定，叠加国内经济复苏及流动性释放预期，因此对商品市场以反弹行情看待。如果接下来国内经济数据转暖得到确认，那么大宗商品仍有进一步反弹的空间。</span><br/><span class="section-news">3. 一德期货宏观分析师肖利娜：在经济弱复苏形势下，降准对大宗商品市场整体影响有限，但与房地产相关的黑色品种或在短期内受到明显提振。</span>'},
  'important': 0,
  'tags': [],
  'channel': [4, 5],
  'remark': []}]
```

- 查询经济数据
```python
data = requests.get('http://127.0.0.1:7000/trade/ctp/market/event?event_date=2022-11-25').json()
print(data[:1])
[{'actual': '0.4',
  'affect': 0,
  'show_affect': 1,
  'consensus': '0.50',
  'country': '新西兰',
  'id': 271221,
  'indicator_id': 692,
  'name': '零售销售季率',
  'previous': '-2.30',
  'pub_time': '2022-11-24T21:45:00.000Z',
  'revised': None,
  'star': 2,
  'time_period': '第三季度',
  'unit': '%',
  'video_url': None,
  'pub_time_unix': 1669326300,
  'time_status': None}]
```

- 查询行情快照
```python
data = requests.get('http://127.0.0.1:7000/trade/ctp/market/realtime_snap?dtype=中金所').json()
print(data['data']['list'][:1])
[{'categoryCode': '',
  'date': '2022-11-25 15:00:00',
  'highPrice': '3802.6',
  'isQuoteOrder': None,
  'lowPrice': '3751.0',
  'name': '沪深300连续',
  'openPrice': '3760.4',
  'preClosePrice': '',
  'quotationShowName': 'IFC0',
  'quoteCode': 'IFC0',
  'range': '17.2',
  'rangePercent': '0.45%',
  'tradePrice': '3782.2'}]
```

- 查询实时行情
```python
data = requests.get('http://127.0.0.1:7000/trade/ctp/market/realtime_hq?code=CNH').json()
print(data)
{'code': 0,
 'message': '成功',
 'data': [{'categoryCode': '',
   'date': '2022-11-26 05:55:00',
   'highPrice': '7.2126',
   'isQuoteOrder': None,
   'lowPrice': '7.1475',
   'name': '离岸人民币',
   'openPrice': '7.1692',
   'preClosePrice': '7.1697',
   'quotationShowName': 'USDCNH',
   'quoteCode': 'CNH',
   'range': '0.0207',
   'rangePercent': '0.28%',
   'tradePrice': '7.1904'}],
 'rollback': False}
```

### 交易功能

- 获取资金
```python
data = requests.get('http://127.0.0.1:7000/trade/ctp/get_account').json()
{'balance': 20011519.72, 'margin': 0.0, 'available': 20011519.72}
```

- 下限价单
```python
data = requests.get('http://127.0.0.1:7000/trade/ctp/order_limit?code=MA301&direction=long&volume=6&price=2600').json()
data = requests.get('http://127.0.0.1:7000/trade/ctp/order_limit?code=sc2302&direction=long&volume=1&price=600').json()
```

- 获取持仓
```python
data = requests.get('http://127.0.0.1:7000/trade/ctp/get_postion').json()

[
    {'code': 'MA301',
    'direction': 'long',
    'volume': 6,
    'margin': 24883.2,
    'cost': 155520.0},
    {'code': 'sc2302',
    'direction': 'long',
    'volume': 1,
    'margin': 97580.00000000001,
    'cost': 574000.0}
]
```

- 查看今日订单
```python
data = requests.get('http://127.0.0.1:7000/trade/ctp/get_orders').json()
  
[
    {'code': 'MA301',
    'direction': 'long',
    'volume': 6,
    'margin': 24883.2,
    'cost': 155520.0},
    {'code': 'sc2302',
    'direction': 'long',
    'volume': 1,
    'margin': 97580.00000000001,
    'cost': 574000.0}
]
```

  - 撤单
  ```python
  data = requests.get('http://127.0.0.1:7000/trade/ctp/order_limit?code=MA301&direction=long&volume=6&price=2500').json()
  print(data)
  # '       36554@MA301'
  data = requests.get('http://127.0.0.1:7000/trade/ctp/order_delete?order_id=       36554@MA301').json()
  ```

---

欢迎关注我的公众号“**量化实战**”，原创技术文章第一时间推送。
