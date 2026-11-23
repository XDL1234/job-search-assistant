"""只通过已绑定岗位页的可见筛选控件应用条件。"""
import re
import unicodedata
from urllib.parse import parse_qsl, urlsplit

from .job_preferences import JOB_TYPES, normalize_filters


GROUPS = {
    'job_type': '求职类型', 'salary': '薪资待遇', 'experience': '工作经验',
    'degree': '学历要求', 'industries': '公司行业', 'company_size': '公司规模', 'financing': '融资阶段',
    'positions': '职位类型',
}
JOBS_PATH = '/web/geek/jobs'
HOME_PATH = re.compile(r'/[a-z]+(?:-[a-z]+)*/')


def city_key(value):
    """BOSS 常省略市级行政区名称末尾的「市」。"""
    label = unicodedata.normalize('NFKC', value or '').strip()
    return label[:-1] if re.fullmatch(r'[\u3400-\u9fff]{2,}市', label) else label


def same_city(actual, requested):
    actual = city_key(actual)
    return bool(actual and actual == city_key(requested))


# 固定代码插入受同源和过期检查保护的 Browser.evaluate。页面标签仅作为数据比较。
FILTER_DOM = r'''
  if (args.step==='filter_dom') {
    const homeStep=['home_dialog','home_toggle','home_province_toggle','home_province',
      'home_city_toggle','home_city'].includes(args.kind);
    const cityHome=path=>/^\/[a-z]+(?:-[a-z]+)*\/$/.test(path);
    if (window!==window.top || (homeStep
      ? !(args.home_path==='/' ? location.pathname==='/' || cityHome(location.pathname)
        : cityHome(args.home_path) && location.pathname===args.home_path)
      : location.pathname.replace(/\/$/,'')!=='/web/geek/jobs'))
      throw Error('BOSS 筛选只支持绑定的职位列表和公开城市首页');
    const norm=value=>String(value??'').normalize('NFKC').replace(/\s+/g,' ').trim();
    const cityKey=value=>norm(value).replace(/^([\u3400-\u9fff]{2,})市$/,'$1');
    const provinceKey=value=>norm(value).replace(/^([\u3400-\u9fff]{2,})省$/,'$1');
    const raw=node=>norm(text(node));
    const pick=(root,css)=>Array.from(root?.querySelectorAll(css)||[]).filter(shown);
    const innermost=nodes=>nodes.filter(node=>!nodes.some(other=>other!==node && node.contains(other)));
    const find=(root,css,label)=>{
      return innermost(pick(root,css).filter(node=>raw(node)===norm(label)));
    };
    const unique=(nodes,kind)=>{
      if (nodes.length>1) throw Error('筛选控件无法唯一确认：'+kind);
      return nodes[0];
    };
    const hasClass=(node,...names)=>names.some(name=>node?.classList.contains(name));
    const active=node=>!!node && (hasClass(node,'active','selected','is-selected','checked','is-checked','is-check') ||
      ['aria-selected','aria-checked','aria-pressed'].some(attr=>node.getAttribute(attr)==='true') ||
      !!node.querySelector('input:checked,[aria-checked="true"],[aria-selected="true"],.active,.selected,.is-selected'));
    function target(node, verb='click', selected=active(node), scroll=false) {
      let point;
      if (node && args.prepare) {
        const center=()=>{const {x,y,width,height}=node.getBoundingClientRect();return [x+width/2,y+height/2];};
        const [x,y]=center();
        if (scroll || x<0 || y<0 || x>=innerWidth || y>=innerHeight) node.scrollIntoView({block:'nearest',inline:'nearest'});
        point=center();
        const hit=document.elementFromPoint(...point);
        if (!node.contains(hit)) return {url:location.href, found:false, blocked:true, selected};
      }
      const result={url:location.href, found:!!node, selected};
      if (node && args.prepare) Object.assign(result,{[verb+'_selector']:mark(node),point});
      return result;
    }
    function publicHome(link) {
      if (!link || !link.getAttribute('href')) return;
      const url = new URL(link.href, location.href);
      if (url.origin===location.origin && cityHome(url.pathname) && !url.username && !url.password) return url;
    }
    if (args.kind==='home_link') {
      const links=[...new Set(find(document,'header a,#header a,.nav a','首页')
        .map(link=>publicHome(link)?.pathname).filter(Boolean))];
      return {url:location.href,home_path:unique(links,'城市首页')};
    }
    if (homeStep) {
      const dialog=unique(pick(document,'.dialog-wrap.change-city-dialog'),'首页城市弹层');
      if (args.kind==='home_dialog') return {url:location.href,ready:!!dialog};
      if (args.kind==='home_toggle') return target(unique(pick(document,'.switchover-city')
        .filter(node=>/^\[?切换\]?$/.test(raw(node))),'首页城市切换'));
      const provinceGroup=unique(pick(dialog,'.city-cascade-input.province'),'省份下拉框');
      const current=unique(pick(provinceGroup,'.city-current'),'当前省份');
      if (args.kind==='home_province_toggle') return target(current);
      const chosen=provinceKey(raw(current))===provinceKey(args.province);
      if (args.kind==='home_province') {
        const options=pick(provinceGroup,'.dropdown-list li');
        const exact=options.filter(node=>raw(node)===norm(args.province));
        const option=unique(exact.length ? exact : options.filter(node=>provinceKey(raw(node))===provinceKey(args.province)),
          '省份 '+args.province);
        return {...target(option,'click',chosen,true), options:options.length};
      }
      const cityGroup=unique(pick(dialog,'.city-cascade-input.city.show'),'省内城市下拉框');
      if (args.kind==='home_city_toggle') return target(chosen ? unique(pick(cityGroup,'.city-current'),'省内城市') : null);
      const options=chosen ? pick(cityGroup,'.dropdown-list a') : [];
      const exact=options.filter(node=>raw(node)===norm(args.label));
      const leaf=unique(exact.length ? exact : options.filter(node=>cityKey(raw(node))===cityKey(args.label)),'省内城市 '+args.label);
      const id=publicHome(leaf) && /^city_select_click_(\d+)$/.exec(leaf.getAttribute('ka')||'')?.[1];
      return {url:location.href,found:!!id,city_code:id};
    }
    const city=unique(pick(document,'.city-label .cur-city-label'), '城市') || unique(pick(document,'.city-label'), '城市');
    const area=unique(pick(document,'.city-area-label .cur-area-label'), '工作区域');
    const toolbar=unique(pick(document,'.c-filter-condition.filter-condition-inner'),'岗位筛选栏');
    const labels=pick(toolbar,'.condition-filter-select > .current-select,.condition-industry-select > .current-select,.condition-position-select > .current-select');
    const groupLabel=node=>raw(node).replace(/\s*\(\s*(\d+)\s*\)$/, '($1)');
    const groupName=node=>norm(get(node,'.placeholder-text')?.textContent)||groupLabel(node).replace(/\([0-9]+\)$/,'');
    const groupCount=node=>node ? Number(raw(get(node,'.select-num')).match(/^\((\d+)\)$/)?.[1]||0) : null;
    const group=unique(labels.filter(node=>args.group && groupName(node)===norm(args.group)),'筛选组 '+args.group);
    const cityName=raw(city);
    const search=unique(pick(document,'input[placeholder*="搜索职位"]:not([readonly])'),'岗位搜索框');
    if (args.kind==='state') return {url:location.href,city:cityName,query:search?.value??null,
      labels:labels.map(groupLabel),area:groupLabel(area)};
    if (args.kind==='query') return {...target(search,'input'),value:search?.value??null};
    if (args.kind==='search_submit') {
      for (let root=search?.parentElement, depth=0; root && depth<5; root=root.parentElement, depth++) {
        const buttons=find(root,'button,a,[role="button"],.search-btn,.search-button','搜索');
        if (buttons.length) return target(unique(buttons,'岗位搜索按钮'));
      }
      return target(null);
    }
    if (args.kind==='city_trigger') return target(city);
    if (args.kind==='hover_reset') return target(search,'hover');
    if (args.kind==='geo_trigger') return {...target(area,'hover'),hovered:area?.matches(':hover')===true};
    if (args.kind==='trigger') return {...target(group,'hover'),hovered:group?.matches(':hover')===true,label:groupLabel(group)};
    const cities=unique(pick(document,'.city-select-dialog'),'城市弹窗');
    if (args.kind==='city_dialog') return {url:location.href, ready:!!cities};
    const cityTabs=pick(cities,'.city-select-wrapper .city-char-list > li');
    if (args.kind==='city_tabs') return {url:location.href, tabs:cityTabs.map(raw).filter(label=>/^[A-Z]{2,8}$/.test(label))};
    if (args.kind==='city_tab') return target(unique(cityTabs.filter(node=>raw(node)===args.label),'城市字母分组'));
    if (args.kind==='city_input') return target(unique(pick(cities,'input:not([readonly])'),'城市搜索框'),'input');
    if (args.kind==='city') {
      const selected=!!cityName && cityKey(cityName)===cityKey(args.label);
      const results=unique(pick(cities,'.city-search-list,.city-search-results,.search-result-list'),'城市搜索结果');
      const cityRoot=results||get(cities,'.city-select-wrapper');
      const options=pick(cityRoot,'li,a,button,[role="option"]').filter(node=>!node.closest('.city-char-list'));
      const exact=innermost(options.filter(node=>raw(node)===norm(args.label)));
      const found=exact.length ? exact : innermost(options.filter(node=>cityKey(raw(node))===cityKey(args.label)));
      return target(unique(found,'城市 '+args.label),'click',selected);
    }
    const areaPanel=unique(pick(area?.parentElement,'.city-area-dropdown'),'工作区域弹出层');
    if (args.kind==='geo_tab') return target(unique(find(areaPanel,'.city-area-tab > li',args.label),'地理筛选方式'));
    if (args.kind==='business_expand') return target(unique(find(areaPanel,'.has-expand .expand-btn','展开全部'),'展开全部商圈'));
    if (args.kind==='geo_state') return {url:location.href, ready:!!areaPanel,
      areas:pick(areaPanel,'.selected-area-section li').map(row=>({
        area:raw(get(row,'.selected-area-name')).replace(/[:：]$/,''),
        selected:pick(row,'.selected-area-list > a').map(raw)
      }))};
    if (args.kind==='geo_clear') return target(unique(find(areaPanel,'.selected-area-section .clear-area-btn','清空已选位置'),'清空已选位置'), 'click', groupLabel(area)==='工作区域');
    const direct=node=>norm(Array.from(node?.childNodes||[]).filter(child=>child.nodeType===3).map(child=>child.textContent).join(''));
    const subway='.subway-select-wrapper';
    const lines=pick(areaPanel,subway+' .subway-line-list > li');
    const stations=pick(areaPanel,subway+' .subway-site-list .subway-site-box');
    const allLine=unique(pick(areaPanel,subway+' .all-line-checkbox'),'地铁全线');
    const wholeLine=active(allLine)||!!get(allLine,'.is-check,.is-checked,.checked');
    if (args.kind==='metro_line') return target(unique(lines.filter(node=>direct(node)===norm(args.label)),'地铁线路 '+args.label));
    if (args.kind==='metro_all') return target(allLine,'click',wholeLine);
    if (args.kind==='metro_station') return target(unique(stations.filter(node=>raw(node)===norm(args.label)),'地铁站 '+args.label));
    if (args.kind==='metro_state') return {url:location.href,ready:lines.length>0,
      current:direct(lines.find(node=>hasClass(node,'active'))), whole:wholeLine, lines:lines.map(direct),
      checkedLines:lines.filter(node=>shown(get(node,'.selected-num'))).map(direct),
      stations:stations.filter(active).map(raw)};
    if (['district','business_area'].includes(args.kind)) {
      const css=args.kind==='district' ? '.dropdown-area-list:not(.business-area-list) > li' : '.business-area-list > li';
      const option=unique(pick(areaPanel,css).filter(node=>direct(node)===norm(args.label)),'工作区域 '+args.label);
      if (args.kind==='business_area' && option) {
        const box=option.getBoundingClientRect(), dropdown=option.closest('.has-expand')?.getBoundingClientRect();
        if (dropdown && box.top+box.height/2>dropdown.bottom && pick(areaPanel,'.has-expand .expand-btn').length===1)
          return {...target(null), expand:true};
      }
      return target(option,'click',active(option) || (args.label==='不限' && groupLabel(area)==='工作区域'));
    }
    const root=group?.parentElement;
    if (args.kind==='option') {
      const choice=unique(find(root,'.filter-select-dropdown ul > li',args.label),'筛选项 '+args.label);
      const count=groupCount(group);
      // BOSS 岗位地址已记录这三种求职类型，已选中时无需重复触发悬停菜单。
      const typeCode=new Map([['全职','1901'],['实习','1902'],['兼职','1903']]).get(args.label);
      if (!choice && args.group==='求职类型' && count===1 && typeCode &&
          new URL(location.href).searchParams.get('jobType')===typeCode)
        return {url:location.href,found:true,selected:true,count};
      return {...target(choice,'click',active(choice) || (args.label==='不限' && count===0)),count};
    }
    const industryOptions='.filter-select-dropdown li.clearfix span,.filter-select-dropdown li.clearfix a,.filter-select-dropdown li.clearfix button';
    if (args.kind==='industry') return target(unique(find(root,industryOptions,args.label),'行业 '+args.label));
    if (args.kind==='industry_selected') return {url:location.href,
      ready:pick(root,'.filter-select-dropdown').length===1,
      selected:pick(root,industryOptions).filter(node=>active(node)&&raw(node)).map(raw)};
    if (args.kind==='position' || args.kind==='position_reset') {
      const path=args.path||[], level=path.length;
      const sidebar=unique(pick(root,'.filter-select-dropdown > ul'),'职位大类列表');
      if (args.kind==='position_reset') return target(unique(find(sidebar,'li','不限'),'不限职位类型'),'click',groupLabel(group)===args.group);
      if (level===1) {
        const category=unique(find(sidebar, 'li', path[0]), '职位大类 '+path[0]);
        return args.browse ? target(category,'hover') : target(category?.querySelector('.icon-arrow') ? null : category);
      }
      const pane=unique(pick(root,'.condition-position-detail'),'职位分类面板');
      const header=unique(pick(pane,'.condition-position-title'),'职位分类标题');
      if (raw(header)!==norm(path[0])) return {url:location.href,found:false};
      if (level===2) return target(unique(find(pane,':scope > .condition-position-list > a',path[1]),'职位选项 '+path[1]));
      const sub=unique(find(pane,'.condition-position-grandson > .condition-position-label',path[1]),'职位子类 '+path[1]);
      return target(unique(find(sub?.parentElement,'.condition-position-list > a',path[2]),'职位选项 '+path[2]));
    }
    throw Error('未知的筛选控件');
  }
'''


def platform_scope(value):
    """投递列表和核验动作可比较的实际地址；参数值由页面读回。"""
    parsed = urlsplit(value)
    if (parsed.path.removesuffix('/') != JOBS_PATH or parsed.scheme != 'https' or parsed.hostname != 'www.zhipin.com'
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('筛选结果不在 BOSS 岗位搜索页')
    return {'path': JOBS_PATH, 'params': [list(item) for item in sorted(parse_qsl(parsed.query, keep_blank_values=True))]}


def selected_city_code(url):
    found = [value for key, value in platform_scope(url)['params'] if key == 'city']
    return found[0] if len(found) == 1 and re.fullmatch(r'[0-9]+', found[0]) else None


def _check_values(values):
    if not isinstance(values, dict):
        raise ValueError('BOSS 筛选条件无效')
    job_type = values.get('job_type')
    if job_type is not None and job_type not in JOB_TYPES.values():
        raise ValueError('BOSS 求职类型必须是全职、兼职、实习或不限')
    cleaned = normalize_filters({key: value for key, value in values.items() if key != 'job_type'}, 'full_time')
    if job_type:
        cleaned['job_type'] = job_type
    return cleaned


def verify_geography_scope(params, values):
    """BOSS 以互斥的范围参数回读地区和地铁条件。"""
    if not {'district', 'metro_line'} & values.keys():
        return
    applied = {key for key, value in params if value}
    if 'district' in values:
        if 'multiSubway' in applied:
            raise ValueError('BOSS 地址中的工作区域与请求不一致；请先清除旧地铁条件再核验')
        if ('multiBusinessDistrict' in applied) != (values['district'] != '不限'):
            raise ValueError('BOSS 地址中的工作区域与请求不一致；请在页面核对已选区域')
    elif 'multiBusinessDistrict' in applied or 'multiSubway' not in applied:
        raise ValueError('BOSS 地址中的地铁范围与请求不一致；请核对页面选中的地区和地铁')


class FilterControls:
    def __init__(self, browser):
        self.browser = browser

    def get(self, kind, **args):
        return self.browser.evaluate('filter_dom', kind=kind, **args)

    def wait(self, kind, ready, message, **args):
        state = self.browser.wait('filter_dom', ready, kind=kind, **args)
        if not ready(state):
            raise ValueError(message)
        return state

    def find(self, kind, **args):
        description = args.get('label') or args.get('province') or ' / '.join(args.get('path', [])) or args.get('group') or kind
        if kind == 'position' and not args.get('browse'):
            description += '；请填写到 BOSS 可勾选职位为止的完整分类路径'
        return self.wait(kind, lambda row: row.get('found') is True,
                         'BOSS 未找到可唯一确认的筛选控件：' + description, prepare=True, **args)

    def trigger(self, kind, **args):
        prepared = self.find(kind, **args)
        if prepared.get('hovered'):
            # Chrome 可在重载后保留悬停位置，移开后才能重新触发弹层。
            self.browser.filter_input(self.find('hover_reset'))
            prepared = self.find(kind, **args)
        self.browser.filter_input(prepared)

    def select(self, kind, opener=None, selected=True, verify=False, **args):
        state = self.get(kind, prepare=not verify, **args)
        if not state.get('found'):
            if opener and not state.get('blocked'):
                opener()
            state = self.get(kind, prepare=not verify, **args)
            if state.get('expand'):
                self.trigger('business_expand')
            if not state.get('found') and not verify:
                state = self.find(kind, **args)
        if verify:
            if state.get('found') and state.get('selected') is selected:
                return state
            return self.wait(kind, lambda row: row.get('found') and row.get('selected') is selected,
                             'BOSS 最终复核未选中：' + (args.get('label') or ' / '.join(args.get('path', [])) or kind), **args)
        if state.get('selected') is not selected:
            self.browser.filter_input(state)
            state = self.get(kind, **args)
            if state.get('found') and state.get('selected') is selected:
                return state
            if not state.get('found') and opener:
                opener()
            return self.wait(kind, lambda item: item.get('selected') is selected and (selected or item.get('found')),
                             'BOSS 无法读回已选筛选项：' + (args.get('label') or ' / '.join(args.get('path', [])) or kind), **args)
        return state

    def province_city(self, city, province, original_url):
        platform_scope(original_url)
        home_path = self.get('home_link').get('home_path') or '/'
        if home_path != '/' and not HOME_PATH.fullmatch(home_path):
            raise ValueError('BOSS 职位页未提供可核验省市的城市首页，请编辑任务核对省市')
        home = {'home_path': home_path}
        province_args = {**home, 'province': province}
        city_args = {**province_args, 'label': city}

        def open_home_control(kind, **args):
            toggle = self.find(kind, **args)
            self.browser.filter_input({'hover_selector': toggle['click_selector'], 'point': toggle.get('point')})
            self.trigger(kind, **args)

        try:
            self.browser.command('open', 'https://www.zhipin.com' + home_path)
            if not self.get('home_dialog', **home).get('ready'):
                open_home_control('home_toggle', **home)
            self.wait('home_dialog', lambda item: item.get('ready'), 'BOSS 未显示首页省市选择框', **home)
            option = self.get('home_province', prepare=True, **province_args)
            if not option.get('selected'):
                if not option.get('found'):
                    if not option.get('options'):
                        open_home_control('home_province_toggle', **home)
                    option = self.browser.wait('filter_dom', lambda item: item.get('found'),
                                               kind='home_province', prepare=True, **province_args)
                    if not option.get('found'):
                        if option.get('blocked'):
                            raise ValueError('BOSS 省份选项被页面遮挡：' + province)
                        if not option.get('options'):
                            raise ValueError('BOSS 未展开省份选项，请检查首页的城市选择框')
                        raise ValueError('BOSS 省份列表中未找到：' + province)
                self.browser.filter_input(option)
                self.wait('home_province', lambda item: item.get('selected'),
                          'BOSS 无法核验已选省份：' + province, **province_args)
            chosen_city = self.get('home_city', **city_args)
            if not chosen_city.get('found'):
                open_home_control('home_city_toggle', **province_args)
                chosen_city = self.wait('home_city', lambda item: item.get('found'),
                                        f'BOSS 无法核验「{province}」下的城市：{city}', **city_args)
            code = chosen_city.get('city_code') or ''
            if not re.fullmatch(r'[0-9]+', code):
                raise ValueError('BOSS 未提供可核验的省内城市标识：' + city)
            return code
        except ValueError as error:
            raise ValueError(f'未能核验「{province} · {city}」，请编辑任务核对省市后重试。{error}') from error
        finally:
            self.browser.verify_target()
            self.browser.command('open', original_url)

    def city(self, city, province, state):
        required_code = self.province_city(city, province, state['url']) if province else None

        def read_city(item):
            params = platform_scope(item['url'])['params']
            return same_city(item.get('city'), city) and (
                selected_city_code(item['url']) == required_code if required_code else
                any(key == 'city' and value for key, value in params))

        if read_city(state):
            return required_code
        if not self.get('city_dialog').get('ready'):
            self.trigger('city_trigger')
        self.wait('city_dialog', lambda row: row.get('ready'), 'BOSS 未显示城市选择弹窗')
        search = self.get('city_input', prepare=True)
        if search.get('found'):
            self.browser.filter_input(search, city_key(city))
        args = {'label': city}
        choice = self.get('city', prepare=True, **args)
        if not choice.get('found') and not search.get('found'):
            for group in self.get('city_tabs').get('tabs', []):
                self.select('city_tab', label=group)
                choice = self.get('city', prepare=True, **args)
                if choice.get('found'):
                    break
        if not choice.get('found'):
            choice = self.find('city', **args)
        self.browser.filter_input(choice)
        self.wait('state', read_city,
                  f'BOSS 无法核验已选城市属于「{province}」：{city}' if province else 'BOSS 无法读回已选城市：' + city)
        return required_code

    def query(self, value):
        field = self.find('query')
        scope = platform_scope(field['url'])
        if field.get('value') == value and ['query', value] in scope['params']:
            return
        self.browser.filter_input(field, value)
        self.trigger('search_submit')
        self.wait('state', lambda item: (item.get('query') == value and ['query', value] in platform_scope(item['url'])['params']),
                  'BOSS 无法读回已执行的关键词搜索：' + value)

    def geography(self, values, verify=False):
        opener = lambda: self.trigger('geo_trigger')
        geo_keys = ('district', 'business_area') if 'district' in values else ('metro_line', 'metro_station')
        tab = '城市和区域' if 'district' in values else '地铁'

        def open_tab():
            self.select('geo_tab', opener, label=tab)

        if geo_keys[0] not in values:
            return
        open_tab()
        if geo_keys[0] == 'metro_line':
            self.metro(values, open_tab, verify)
            return
        district = values['district']
        wanted = [] if district == '不限' else [{'area': district, 'selected': [values.get('business_area') or '全' + district]}]
        current = self.wait('geo_state', lambda row: row.get('ready'), 'BOSS 未显示工作区域选择面板')
        if current['areas'] == wanted:
            return
        if verify:
            raise ValueError('BOSS 最终复核的行政区或商圈与要求不一致')
        if current['areas']:
            self.select('geo_clear', open_tab)
        for key in geo_keys:
            if key in values:
                self.select(key, open_tab, label=values[key])
        open_tab()
        self.wait('geo_state', lambda row: row.get('ready') and row.get('areas') == wanted,
                  'BOSS 已选行政区或商圈与请求不一致')

    def metro(self, values, open_tab, verify):
        requested_line = values['metro_line']
        available = self.wait('metro_state', lambda row: row.get('ready'), 'BOSS 未显示地铁线路列表')['lines']
        if requested_line in available:
            line = requested_line
        else:
            matches = [name for name in available if requested_line in name.split('/')]
            if len(matches) != 1:
                raise ValueError('BOSS 未找到可唯一确认的地铁线路：' + requested_line)
            line = matches[0]
        station = values.get('metro_station')

        def open_line(name):
            self.select('metro_line', open_tab, label=name)
            return self.wait('metro_state', lambda row: row.get('ready') and row.get('current') == name,
                             'BOSS 未显示地铁线路：' + name)

        current = open_line(line)
        if not verify:
            for other in current['checkedLines']:
                state = open_line(other)
                if other == line and (state['whole'] if not station else (not state['whole'] and state['stations'] == [station])):
                    continue
                if state['whole']:
                    self.select('metro_all', lambda: open_line(other), selected=False)
                else:
                    for extra in state['stations']:
                        if other != line or extra != station:
                            self.select('metro_station', lambda: open_line(other), selected=False, label=extra)
            open_line(line)
        opener = lambda: open_line(line)
        if station:
            self.select('metro_station', opener, label=station, verify=verify)
        else:
            self.select('metro_all', opener, verify=verify)
        checked = open_line(line)
        if (checked['checkedLines'] != [line] or (checked['whole'] if station else not checked['whole'])
                or (station and checked['stations'] != [station])):
            raise ValueError('BOSS 最终复核的地铁线路或站点与要求不一致')

    def choices(self, key, values, verify=False):
        group = GROUPS[key]
        opener = lambda: self.trigger('trigger', group=group)
        if key == 'positions':
            def choose_paths():
                for path in values:
                    def open_path():
                        opener()
                        if len(path) > 1:
                            self.trigger('position', group=group, path=path[:1], browse=True)
                    self.select('position', open_path, group=group, path=path, verify=verify)

            if values == [['不限']]:
                self.select('position_reset', opener, group=group, label='不限', verify=verify)
                return
            choose_paths()
            count = selected_count(self.get('state')['labels'], group)
            if not verify and count is not None and count > len(values):
                self.select('position_reset', opener, group=group, label='不限')
                choose_paths()
        elif key == 'industries':
            chosen = [] if values == ['不限'] else values
            opener()
            current = self.wait('industry_selected', lambda row: row.get('ready'), 'BOSS 未显示公司行业选择面板', group=group)
            if verify and set(current['selected']) != set(chosen):
                raise ValueError('BOSS 最终复核的公司行业与要求不一致')
            for extra in dict.fromkeys(current['selected']):
                if extra not in chosen:
                    self.select('industry', opener, selected=False, group=group, label=extra)
            for value in chosen:
                self.select('industry', opener, group=group, label=value, verify=verify)
        else:
            actual = self.select('option', opener, group=group, label=values, verify=verify)
            if not verify and values != '不限' and (actual.get('count') or 0) > 1:
                self.select('option', opener, group=group, label='不限')
                self.select('option', opener, group=group, label=values)


def selected_count(labels, group):
    for label in labels:
        match = re.fullmatch(re.escape(group) + r'(?:\(([0-9]+)\))?', label)
        if match:
            return int(match[1] or 0)
    return None


def apply_filters(browser, values):
    """选定条件并读回实际岗位列表范围；不打开详情、不沟通、不提交。"""
    values = _check_values(values)
    controls = FilterControls(browser)
    tab = browser.verify_target()
    if urlsplit(tab['url']).path.removesuffix('/') != JOBS_PATH:
        browser.command('open', 'https://www.zhipin.com' + JOBS_PATH)
    first = controls.get('state')
    def missing_controls(state):
        missing = [group for key, group in GROUPS.items() if key in values
                   and selected_count(state['labels'], group) is None]
        if not state['area'] and {'district', 'metro_line'} & values.keys():
            missing.append('工作区域')
        return missing

    city_code = None
    if 'city' in values:
        city_code = controls.city(values['city'], values.get('province'), first)
    if 'query' in values:
        controls.query(values['query'])
    elif first.get('query') and 'city' in values:
        controls.query(first['query'])
    if 'job_type' in values:
        controls.choices('job_type', values['job_type'])
    dependent = (GROUPS.keys() - {'job_type'}) | {'district', 'metro_line'}
    if dependent & values.keys():
        state = browser.wait('filter_dom', lambda state: not missing_controls(state), kind='state')
        missing = missing_controls(state)
        if missing:
            detail = 'BOSS 当前求职类型或页面未提供筛选：' + '、'.join(missing)
            if not state.get('query'):
                detail += '。可先填写搜索关键词以打开完整筛选栏'
            raise ValueError(detail)
    controls.geography(values)
    for key in GROUPS:
        if key in values and key != 'job_type':
            controls.choices(key, values[key])
    before_readback = platform_scope(controls.get('state')['url'])
    # 从已提交的列表地址重新加载，避免把临时选中状态当作平台结果。
    browser.command('reload')
    controls.wait('state', lambda state: bool(state['labels']) and not missing_controls(state),
                  'BOSS 未从已提交的地址恢复筛选栏')
    controls.geography(values, verify=True)
    for key in GROUPS:
        if key in values:
            controls.choices(key, values[key], verify=True)
    final = controls.get('state')
    scope = platform_scope(final['url'])
    if scope != before_readback:
        raise ValueError('BOSS 筛选范围在最终核对时发生变化')
    verify_geography_scope(scope['params'], values)
    if 'city' in values and not same_city(final['city'], values['city']):
        raise ValueError('其他筛选操作改变了已核验的城市')
    if city_code and selected_city_code(final['url']) != city_code:
        raise ValueError('其他筛选操作改变了已核验的省份和城市')
    if 'query' in values and (final['query'] != values['query'] or ['query', values['query']] not in scope['params']):
        raise ValueError('其他筛选操作改变了已核验的搜索关键词')
    for key, group in GROUPS.items():
        if key in values:
            value = values[key]
            expected = 0 if value in ('不限', ['不限'], [['不限']]) else len(value) if isinstance(value, list) else 1
            if selected_count(final['labels'], group) != expected:
                raise ValueError('BOSS 筛选项数量与要求不一致：' + group)
    return {'url': final['url'], 'scope': scope, 'filters': values,
            'city': final['city'], 'labels': final['labels'], 'area': final['area']}
