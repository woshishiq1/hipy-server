#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File  : vod_tool.py
# Author: DaShenHan&道长-----先苦后甜，任凭晚风拂柳颜------
# Date  : 2024/2/5
import re

import ujson
from time import time
import base64
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import requests
import warnings

# 关闭警告
warnings.filterwarnings("ignore")
requests.packages.urllib3.disable_warnings()

_cloudfare_enable = False
try:
    import cloudscraper

    scraper = cloudscraper.create_scraper()
    _cloudfare_enable = True
except ImportError:
    pass


def base_request(_url, _object, _js_type=0, cloudfare=False):
    """
    基础网络请求封装，兼容qjs和pythonmonkey引擎二次使用
    @param _url:网页地址
    @param _object: 必要参数字典 method,timeout,body,data,headers,withHeaders 等
    @param _js_type: 0 qjs 1 pythonmonkey
    @param cloudfare: 使用过5秒盾请求
    @return:
    """
    # print(f'_url:{_url},_object:{_object},_js_type:{_js_type}')
    if cloudfare and _cloudfare_enable:
        s = scraper
    else:
        s = requests
    if not isinstance(_object, dict) and _js_type == 0:
        _object = ujson.loads(_object.json())
    elif _js_type == 1:
        _object = dict(_object)

    method = (_object.get('method') or 'get').lower()
    timeout = _object.get('timeout') or 5
    max_timeout = 30
    if max_timeout < timeout < 100:
        # 限制最大30秒超时。如果传的数值大于这个但是小于100，说明错误，自动变成30
        timeout = max_timeout
    elif timeout >= 100:
        # 限制最大30秒超时。如果传的数值大于100，说明是把毫秒和秒理解错误，自动除以1000
        timeout = round(timeout / 1000, 2)
    # print(f'timeout:{timeout}')
    body = _object.get('body') or ''
    encoding = _object.get('encoding') or 'utf-8'
    data = _object.get('data') or {}
    _headers = _object.get('headers') or {}
    _headers = dict(_headers)
    headers = {}
    for key, value in _headers.items():
        headers[str(key).lower()] = str(value)

    if headers.get('content-type') and re.search('charset=(.*)', headers['content-type'], re.I):
        try:
            encoding = re.search('charset=(.*)', headers['content-type'], re.I).groups()[0]
        except:
            pass

    if body and not data:
        if '&' in body:
            for p in body.split('&'):
                k = p.split('=')[0]
                v = '='.join(p.split('=')[1:])
                data[k] = v
            # 修复pythonmonkey没有自动把 JSObjectProxy 转为python的dict导致的后续错误
            data = dict(data)
        else:
            data = body

    elif not body and data and method != 'get':
        content_type_keys = [key for key in headers if key.lower() == 'content-type']
        default_type = 'application/json'
        content_type = default_type
        if content_type_keys:
            content_type_key = content_type_keys[-1]
            old_content_type = headers[content_type_key]
            # if content_type not in old_content_type:
            #     headers[content_type_key] = content_type
            content_type = old_content_type
        else:
            headers['Content-Type'] = default_type

        if isinstance(data, dict) and default_type in content_type:
            data = ujson.dumps(data, ensure_ascii=False)

    buffer = _object.get('buffer') or 1
    redirect = False if _object.get('redirect') == 0 or _object.get('redirect') == False else True
    withHeaders = bool(_object.get('withHeaders') or False)
    r = None
    r_text = ''
    r_content = b''
    r_headers = {}
    if method == 'get':
        try:
            r = s.get(_url, allow_redirects=redirect, headers=headers, params=data, timeout=timeout,
                      verify=True if cloudfare else False)
            # r.encoding = r.apparent_encoding
            r.encoding = encoding
            r_text = r.text
            r_content = r.content
            r_headers = dict(r.headers)
            r_headers = {str(key).lower(): str(value) for key, value in r_headers.items()}
        except Exception as e:
            error = f'base_request {method} 发生了错误:{e}'
            r_headers['error'] = error
            print(error)
    else:
        _request = None
        if method == 'post':
            _request = s.post
        elif method == 'put':
            _request = s.put
        elif method == 'delete':
            _request = s.delete
        elif method == 'head':
            _request = s.head

        if _request:
            try:
                r = _request(_url, allow_redirects=redirect, headers=headers, data=data, timeout=timeout,
                             verify=True if cloudfare else False)
                # r.encoding = r.apparent_encoding
                r.encoding = encoding
                r_text = r.text
                r_content = r.content
                r_headers = dict(r.headers)
                r_headers = {str(key).lower(): str(value) for key, value in r_headers.items()}
            except Exception as e:
                error = f'base_request {method} 发生了错误:{e}'
                r_headers['error'] = error
                print(error)
    if 'Just a moment...' in r_text and not cloudfare and _cloudfare_enable:
        # print('遇到了Just a moment...')
        return base_request(_url, _object, _js_type, cloudfare=True)
    if buffer == 2:
        r_text = base64.b64encode(r_content).decode("utf8")
    empty_result = {'content': '', 'headers': {}}
    if withHeaders and _js_type == 0:
        result = {'body': r_text or '', 'headers': r_headers or {}}
        return ujson.dumps(result)
    elif not withHeaders and _js_type == 0:
        return r_text if r_text else ''
    elif _js_type == 1:
        # print(r_text)
        result = {'content': r_text or '', 'headers': r_headers or {}}
        return result
    else:
        return empty_result


def fetch(_url, _object):
    """
    qjs试用的fetch函数
    @param _url:
    @param _object:
    @return:
    """
    return base_request(_url, _object, 0)


def req(_url, _object):
    """
    tvbox注入的pythonmoneky版req函数
    @param _url:
    @param _object:
    @return:
    """
    return base_request(_url, _object, 1)


def batchFetch(items, max_workers=16):
    """
    海阔视界批量请求 [{url:'',options:{}},{url:'',options:{}}]
    batchFetch([{url:'http://www.a.cn', options:{headers:{},body:'a=1&b=2',method:'POST'}}, {url:'http://www.b.cn'}]);setError(data[0] + '=====' + data[1]);
    @param items:
    @param max_workers:
    @return:
    """
    # print(items)
    results = [None for item in items]
    with ThreadPoolExecutor(max_workers=min(len(items), max_workers)) as pool:
        tasks = [pool.submit(fetch, item.get('url'), item.get('options')) for item in
                 items]  # 构造一个列表，循环向线程池内submit提交执行的方法
        try:
            pool.shutdown(wait=True)  # 线程数等待所有线程结束，这里 卡住主线程
            results = [task.result() for task in tasks]
        except Exception as e:
            print(f'执行batchFetch发生了错误:{e}')
    return results


def 重定向(_url: str):
    if _url.startswith('http'):
        return f'redirect://{_url}'
    else:
        return str(_url)


def toast(_url: str):
    return f'toast://{_url}'


def image(_text: str):
    return f'image://{_text}'


def base64ToImage(_image_base64: str):
    if ',' in _image_base64:
        _image_base64 = _image_base64.split(',')[1]
    _img_data = base64.b64decode(_image_base64)
    return _img_data


def get_interval(t):
    interval = time() - t
    interval = round(interval * 1000, 2)
    return interval
