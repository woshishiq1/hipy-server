#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File  : views.py
# Author: DaShenHan&道长-----先苦后甜，任凭晚风拂柳颜------
# Author's Blog: https://blog.csdn.net/qq_32394351
# Date  : 2023/12/7
import base64
import json
import ujson
import os
import re

from fastapi import APIRouter, Request, Depends, Response, Query, File, UploadFile
from fastapi.responses import RedirectResponse
from typing import Any
from sqlalchemy.orm import Session

from .gen_vod import Vod
from common import error_code
from common.resp import respVodJson, respErrorJson, abort
from urllib.parse import quote, unquote
import requests
from apps.permission.models.user import Users
from apps.vod.curd.curd_configs import curd_vod_configs
from apps.vod.curd.curd_subs import curd_vod_subs

from common import deps
from core.logger import logger
from core.constants import BASE_DIR
from utils.path import get_api_path, get_file_text, get_file_modified_time, get_now
from utils.tools import get_md5
from dateutil.relativedelta import relativedelta
from datetime import datetime
from pathlib import Path
import sys
from t4.qjs_drpy.qjs_drpy import Drpy

router = APIRouter()

access_name = 'vod:generate'
api_url = ''
API_STORE = {

}
# 扩展储存同api分发不同ext扩展的上限。超过这个上限清空储存
API_DICT_LIMIT = 5


# u: Users = Depends(deps.user_perm([f"{access_name}:get"]))
@router.api_route(methods=['GET', 'POST', 'HEAD'], path=api_url + "/{api:path}", summary="生成Vod")
def vod_generate(*, api: str = "", request: Request,
                 db: Session = Depends(deps.get_db),
                 ) -> Any:
    """
    这个接口千万不要写async def 否则类似这种内部接口文件请求将无法实现 http://192.168.31.49:5707/files/hipy/两个BT.json
    通过动态import的形式，统一处理vod:爬虫源T4接口
    ext参数默认为空字符串，传入api配置里对应的ext，可以是文本和链接
    """

    # 接口是drpy源
    is_drpy = api.endswith('.js')
    # 缓存初始化结果持续秒数|默认2小时
    _seconds = 60 * 60 * 2
    # _seconds = 10
    global API_STORE

    def getParams(key=None, value=''):
        return request.query_params.get(key) or value

    # 订阅检测
    sub_info = None
    sub = getParams('sub')
    has_sub = curd_vod_subs.isExists(db)
    if has_sub:
        if not sub or len(sub) < 6:
            return respErrorJson(error_code.ERROR_PARAMETER_ERROR.set_msg(f'参数【sub】不正确'))
        sub_record = curd_vod_subs.getByCode(db, sub)
        if not sub_record:
            return respErrorJson(error_code.ERROR_PARAMETER_ERROR.set_msg(f'不存在此订阅码:【{sub}】'))
        if sub_record.status == 0:
            return respErrorJson(error_code.ERROR_PARAMETER_ERROR.set_msg(f'此订阅码:【{sub}】已禁用'))
        if sub_record.due_time:
            current_time = datetime.now()
            if current_time > sub_record.due_time:
                return respErrorJson(error_code.ERROR_NOT_FOUND.set_msg(
                    f'此订阅码【{sub}】已过期。到期时间为:{sub_record.due_time},当前时间为:{current_time.strftime("%Y-%m-%d %H:%M:%S")}'))

        sub_info = sub_record.dict()
    # print('sub_info:', sub_info)
    # 暂不支持使用正则过滤接口的方式限制某个api不允许访问
    has_access = True
    if sub_info.get('mode') == 0:
        has_access = True if re.search(sub_info.get('reg') or '.*', api, re.I) else False
    elif sub_info.get('mode') == 1:
        has_access = True if not re.search(sub_info.get('reg') or '.*', api, re.I) else False
    # print(f'has_access:{has_access}')

    # 拿到query参数的字典
    params_dict = request.query_params.__dict__['_dict']
    # 拿到网页host地址
    host = str(request.base_url).rstrip('/')
    # 拿到完整的链接
    whole_url = str(request.url)
    # 拼接字符串得到t4_api本地代理接口地址
    api_url = str(request.url).split('?')[0]

    t4_api = f'{api_url}?proxy=true&do=py'
    t4_js_api = f'{api_url}?proxy=true&do=js'
    # 获取请求类型
    req_method = request.method.lower()

    # 本地代理所需参数
    proxy = getParams('proxy')
    do = getParams('do')
    # 是否为本地代理请求
    is_proxy = proxy and do in ['py', 'js']

    # 开发者模式会在首页显示内存占用
    debug = getParams('debug')
    # 如果传了nocache就会清除缓存
    nocache = getParams('nocache')

    api_ext = getParams('api_ext')  # t4初始化api的扩展参数
    extend = getParams('extend')  # t4初始化配置里的ext参数
    extend = extend or api_ext

    # 判断head请求但不是本地代理直接干掉
    # if req_method == 'head' and (t4_api + '&') not in whole_url:
    if req_method == 'head' and not is_proxy:
        return abort(403)

    if not is_proxy:
        # 非本地代理请求需要验证密码
        pwd = getParams('pwd')
        try:
            vod_configs_obj = curd_vod_configs.getByKey(db, key='vod_passwd')
            vod_passwd = vod_configs_obj.get('value') if vod_configs_obj.get('status') == 1 else ''
        except Exception as e:
            logger.info(f'获取vod_passwd发生错误:{e}')
            vod_passwd = ''
        if vod_passwd and pwd != vod_passwd:
            return abort(403)

    # 需要初始化
    need_init = False

    # 无法加缓存，不知道怎么回事。多线程访问会报错的
    if is_drpy and nocache and api in API_STORE:
        del API_STORE[api]

    try:
        extend_store = get_md5(extend) if extend else extend
        api_path = get_api_path(api)
        api_time = get_file_modified_time(api_path)
        api_store_lists = list(API_STORE.keys())
        if api not in api_store_lists:
            # 没初始化过，需要初始化
            need_init = True
            # 设为空字典
            API_STORE[api] = {}
        else:
            _api_dict = API_STORE[api] or {}
            _apis = _api_dict.keys()
            # 超过字典扩展分发储存限制自动清空并且要求重新初始化
            if len(_apis) > API_DICT_LIMIT:
                logger.info(f'源路径:{api_path}疑似被恶意加载扩展，超过扩展分发数量{API_DICT_LIMIT}，现在清空储存器')
                # 初始化过，但是扩展数量超过上限。防止恶意无限刷扩展，删了
                need_init = True
                del API_STORE[api]
                # 设为空字典
                API_STORE[api] = {}
            else:
                # 防止储存类型不是字典
                if not isinstance(API_STORE[api], dict):
                    # 设为空字典
                    API_STORE[api] = {}

                # _api = API_STORE[api] or {'time': None}
                # 取拓展里的
                _api = API_STORE[api].get(extend_store) or {'time': None}
                _api_time = _api['time']
                # 内存储存时间 < 文件修改时间 需要重新初始化
                if not _api_time or _api_time < api_time or (_api_time + relativedelta(seconds=_seconds) < get_now()):
                    need_init = True

        if need_init:
            logger.info(f'需要初始化源:源路径:{api_path},扩展:{extend_store},源最后修改时间:{api_time}')
            if is_drpy:
                vod = Drpy(api, t4_js_api, debug)
            else:
                vod = Vod(api=api, query_params=request.query_params, t4_api=t4_api).module
            # 记录初始化时间|下次文件修改后判断储存的时间 < 文件修改时间又会重新初始化
            # API_STORE[api] = {'vod': vod, 'time': get_now()}
            API_STORE[api][extend_store] = {'vod': vod, 'time': get_now()}
        else:
            vod = API_STORE[api][extend_store]['vod']

    except Exception as e:
        return respErrorJson(error_code.ERROR_INTERNAL.set_msg(f"内部服务器错误:{e}"))

    ac = getParams('ac')
    ids = getParams('ids')
    filters = getParams('f')  # t1 筛选 {'cid':'1'}
    ext = getParams('ext')  # t4筛选传入base64加密的json字符串
    filterable = getParams('filter')  # t4能否筛选
    if req_method == 'post':  # t4 ext网络数据太长会自动post,此时强制可筛选
        filterable = True
    wd = getParams('wd')
    quick = getParams('quick')
    play_url = getParams('play_url')  # 类型为t1的时候播放链接带这个进行解析
    play = getParams('play')  # 类型为4的时候点击播放会带上来
    flag = getParams('flag')  # 类型为4的时候点击播放会带上来
    t = getParams('t')
    pg = getParams('pg', '1')
    pg = int(pg)
    q = getParams('q')
    ad_remove = getParams('adRemove')
    ad_url = getParams('url')
    ad_headers = getParams('headers')
    ad_name = getParams('name') or 'm3u8'

    if is_drpy:
        vod.setDebug(debug)

    if need_init and not is_drpy:
        vod.setExtendInfo(extend)

        # 获取依赖项
        depends = vod.getDependence()
        modules = []
        module_names = []
        for lib in depends:
            try:
                module = Vod(api=lib, query_params=request.query_params, t4_api=t4_api).module
                modules.append(module)
                module_names.append(lib)
            except Exception as e:
                logger.info(f'装载依赖{lib}发生错误:{e}')
                # return respErrorJson(error_code.ERROR_INTERNAL.set_msg(f"内部服务器错误:{e}"))

        if len(module_names) > 0:
            logger.info(f'当前依赖列表:{module_names}')

        vod.init(modules)

    elif need_init and is_drpy:
        try:
            js_code = get_file_text(api_path)
            try:
                vod_configs_obj = curd_vod_configs.getByKey(db, key='vod_hipy_env')
                env = vod_configs_obj.get('value')
                env = ujson.loads(env)
            except Exception as e:
                logger.info(f'获取环境变量发生错误:{e}')
                env = {}

            # print(env)
            for k in env.keys():
                if f'${k}' in js_code:
                    js_code = js_code.replace(f'${k}', f'{env[k]}')
            if extend:
                if extend.startswith('http'):
                    logger.info(f'初始化drpy源:{api}使用了ext:{extend}')
                else:
                    logger.info(f'初始化drpy源:{api}使用了ext字符串不是地址，可能会存在意料之外的问题')

                js_code += '\n' + f'rule.params="{extend}";'

            vod.init(js_code)
        except Exception as e:
            logger.info(f'初始化drpy源:{api}发生了错误:{e},下次将会重新初始化')
            del API_STORE[api]

    rule_title = vod.getName()
    if rule_title:
        logger.info(f'加载爬虫源:{rule_title}')

    if ext and not ext.startswith('http'):
        try:
            # ext = json.loads(base64.b64decode(ext).decode("utf-8"))
            filters = base64.b64decode(ext).decode("utf-8")
        except Exception as e:
            logger.error(f'解析发生错误:{e}。未知的ext:{ext}')

    if is_proxy:
        # 测试地址:
        # http://192.168.31.49:5707/api/v1/vod/base_spider?proxy=1&do=py&url=https://s1.bfzycdn.com/video/renmindemingyi/%E7%AC%AC07%E9%9B%86/index.m3u8&adRemove=reg:/video/adjump(.*?)ts
        if ad_remove.startswith('reg:') and ad_url.endswith('.m3u8'):
            headers = {}
            if ad_headers:
                try:
                    headers = json.loads(unquote(ad_headers))
                except:
                    pass

            try:
                r = requests.get(ad_url, headers=headers)
                text = r.text
                # text = vod.replaceAll(text, ad_remove[4:], '')
                m3u8_text = vod.fixAdM3u8(text, ad_url, ad_remove)
                # return Response(status_code=200, media_type='video/MP2T', content=m3u8_text)
                media_type = 'text/plain' if 'txt' in ad_name else 'video/MP2T'
                return Response(status_code=200, media_type=media_type, content=m3u8_text)
            except Exception as e:
                error_msg = f"localProxy执行ad_remove发生内部服务器错误:{e}"
                logger.error(error_msg)
                return respErrorJson(error_code.ERROR_INTERNAL.set_msg(error_msg))

        try:
            back_resp_list = vod.localProxy(params_dict)
            status_code = back_resp_list[0]
            media_type = back_resp_list[1]
            content = back_resp_list[2]
            headers = back_resp_list[3] if len(back_resp_list) > 3 else None
            to_bytes = back_resp_list[4] if len(back_resp_list) > 4 else None
            # if isinstance(content, str):
            #     content = content.encode('utf-8')
            if to_bytes:
                try:
                    if 'base64,' in content:
                        content = unquote(content.split("base64,")[1])
                    content = base64.b64decode(content)
                except Exception as e:
                    logger.error(f'本地代理to_bytes发生了错误:{e}')
            return Response(status_code=status_code, media_type=media_type, content=content, headers=headers)
        except Exception as e:
            error_msg = f"localProxy执行发生内部服务器错误:{e}"
            logger.error(error_msg)
            return respErrorJson(error_code.ERROR_INTERNAL.set_msg(error_msg))

    if play:  # t4播放
        try:
            play_url = vod.playerContent(flag, play, None)

            if isinstance(play_url, str):
                player_dict = {'parse': 0, 'playUrl': '', 'jx': 0, 'url': play_url}
            elif isinstance(play_url, dict):
                player_dict = play_url.copy()
            else:
                return abort(404, f'不支持的返回类型:{type(play_url)}\nplay_url:{play_url}')

            if str(player_dict.get('parse')) == '1' and not player_dict.get('isVideo'):
                player_dict['isVideo'] = vod.isVideo()
            if not player_dict.get('adRemove'):
                player_dict['adRemove'] = vod.adRemove()

            # 有 adRemove参数并且不需要嗅探,并且地址以http开头.m3u8结尾 并且不是本地代理地址
            proxy_url = vod.getProxyUrl()
            if player_dict.get('adRemove') and str(player_dict.get('parse')) == '0' \
                    and str(player_dict.get('url')).startswith('http') and str(player_dict.get('url')).endswith('.m3u8') \
                    and not str(player_dict.get('url')).startswith(proxy_url):
                # 删除字段并给url字段加代理
                adRemove = player_dict['adRemove']
                del player_dict['adRemove']
                player_dict['url'] = proxy_url + '&url=' + player_dict[
                    'url'] + f'&adRemove={quote(adRemove)}&name=1.m3u8'

            return respVodJson(player_dict)

        except Exception as e:
            error_msg = f"playerContent执行发生内部服务器错误:{e}"
            logger.error(error_msg)
            return respErrorJson(error_code.ERROR_INTERNAL.set_msg(error_msg))

    if play_url:  # t1播放
        play_url = vod.playerContent(flag, play_url, vipFlags=None)
        if isinstance(play_url, str):
            return RedirectResponse(play_url, status_code=301)
        elif isinstance(play_url, dict):
            return respVodJson(play_url)
        else:
            return play_url

    if ac and t:  # 一级
        try:
            fl = {}
            if filters and filters.find('{') > -1 and filters.find('}') > -1:
                fl = json.loads(filters)
            # print(filters,type(filters))
            # print(fl,type(fl))
            logger.info(fl)
            if filters:
                filterable = True
            data = vod.categoryContent(t, pg, filterable, fl)
            return respVodJson(data)
        except Exception as e:
            error_msg = f"categoryContent执行发生内部服务器错误:{e}"
            logger.error(error_msg)
            return respErrorJson(error_code.ERROR_INTERNAL.set_msg(error_msg))

    if ac and ids:  # 二级
        try:
            id_list = ids.split(',')
            data = vod.detailContent(id_list)
            try:
                _type = vod.getRule('类型')
                data.update({"type": _type})
            except Exception as e:
                if is_drpy:
                    logger.error(f'二级尝试获取源类型发生错误:{e}')
            return respVodJson(data)
        except Exception as e:
            error_msg = f"detailContent执行发生内部服务器错误:{e}"
            logger.error(error_msg)
            return respErrorJson(error_code.ERROR_INTERNAL.set_msg(error_msg))
    if wd:  # 搜索
        try:
            data = vod.searchContent(wd, quick, pg)
            return respVodJson(data)
        except Exception as e:
            error_msg = f"searchContent执行发生内部服务器错误:{e}"
            logger.error(error_msg)
            return respErrorJson(error_code.ERROR_INTERNAL.set_msg(error_msg))

    home_data = vod.homeContent(filterable) or {}
    home_video_data = vod.homeVideoContent() or {}
    try:
        _type = vod.getRule('类型')
        home_data.update({"type": _type})
    except Exception as e:
        if is_drpy:
            logger.error(f'首页尝试获取源类型发生错误:{e}')
    home_data.update(home_video_data)

    if debug:
        home_data.update({'API_STORE_SIZE': sys.getsizeof(API_STORE)})

    return respVodJson(home_data)
