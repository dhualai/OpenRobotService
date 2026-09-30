// 录入信息 —— 「项目管理 → 新建项目 → 录入信息」进入的项目信息登记页。
//
// 五个字段（2026-09-30 用户口径）：项目名称 / 项目编号 / 项目地点 / 客户名称 / 车型。
// 一条录入 = wechat_qrcodes 里的一行（五个字段和行 id 同行存）：
//   创建 POST /qrcodes/project-info；点条目回到本页（/admin/info-entry/:id）修改走
//   PUT /qrcodes/{id}/project-info。保存后该行在二维码管理里可见、可继续生成 ticket。
//
// 项目id 不再显示（2026-09-30 用户口径）：它就是行 id（str(id)），不单独占列、
//   不随表单提交——新建时保存后自动生成，编辑时也不在界面上露出。
// 扫码跳转链接（…/info-entry/{id}?scene=xxx&openid=…）里的 scene 即 str(id)：
//   - 场景值能查到行 → 就是编辑那一行；
//   - 场景值查不到行（码还没录入过）→ 按新录入处理（项目id 保存后自动生成，
//     不需要、也无法预填）；
//   - 链接没带 scene（管理端手动新建）→ 同样新录入。
//   - 该行已是 published（录入信息保存即发布，后端 create_project_info 直接落
//     QrcodeStatus.PUBLISHED）→ 不停留本页，直接跳「我要摇人」（2026-09-30
//     用户口径）：scene/openid 原样带过去，CallView 按 scene 弹车体信息确认；
//     管理端「编辑信息」链接不带 scene，不受影响（那是修改数据的入口）。
//   注：录入信息相关的三个接口（by-scene / 按 :id 查 / 保存）都是「登录即可」——
//   所有人扫码都能录入信息（2026-09-30 用户口径）；管理端其余接口仍是 admin 权限。
//
// 本页没有「确认信息」按钮（2026-09-30 用户口径）：扫码 → 录入 → 保存即发布，
//   不再需要二次确认这一步。该行 ticket 生成后状态是 entering，此时扫这张码会跳转到
//   本页（后端 _send_scan_redirect_card 按状态分流）；已 published 后再扫同一张码：
//   后端对新卡片本就分流到 /app/call；若点的是生成于 entering 时期的旧卡片、链接落回
//   本页，则由上面的 published 判断兜底重定向。
//
// 规则（界面不写注解，由交互体现）：
// - 项目名称：必填，排在第一位。下拉候选来自 project 表（一次拉全量——含台账里
//   「待定」的未承接项目，见 getProjects 的 includePending——本地模糊匹配 name/编码），
//   选中已有项目会一并带出它的项目编号；直接手打出与表里完全同名的项目，同样按
//   「选中」处理；也能输新名字，保存时由后端补进 project 表（用项目编号当新项目的
//   id/code，见 qrcode.py _ensure_project_row）
// - 项目编号：必填，唯一。名称匹配到 project 表里的项目时，编号以表里为准（自动带出、
//   点编号框也会取一次表里的值）；新项目时手动填写；与已有项目/其他录入行冲突时后端
//   400，detail 直接 Toast 出来
// - 项目地点 / 客户名称 / 车型：必填
//
// tdesign-mobile-react 注意：FormItem 一旦加了 name 属性，就会接管子组件的值管理
// （从 Form 内部 store 读值、拦截 onChange 回写）。本页用手动 useState 管 form，
// 且需要"选项目名 → 自动填项目编号"这种跨字段联动（setForm 绕过 FormItem 拦截链路），
// 所以**所有 FormItem 都不能加 name**，否则联动出来的值会被 FormItem 的空 store 覆盖掉。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Button, Form, FormItem, Loading, Toast } from 'tdesign-mobile-react';
import ClearableInput from '@/shared/components/ClearableInput';
import { getProjects, type ProjectItem } from '@/api/projects';
import {
  createProjectInfo, fetchQrcode, fetchQrcodeByScene, updateProjectInfo,
  type QrcodeItem,
} from '@/api/qrcode';

const errMsg = (err: unknown, fallback: string) =>
  err instanceof Error && err.message ? err.message : fallback;

/** 场景值 = str(id)（2026-09-30 口径），只认纯数字（容错路径手输的链接） */
const SCENE_PATTERN = /^\d{1,10}$/;

const emptyForm = {
  project_code: '',
  project_name: '',
  project_location: '',
  customer_name: '',
  vehicle_model: '',
};

export default function InfoEntry() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const numId = id ? Number(id) : NaN;
  const pathId = Number.isInteger(numId) && numId > 0 ? numId : null;

  // 扫码进入：链接里的 scene 即 str(id)（不合规按没带处理，容错路径手输的链接）
  const sceneCode = useMemo(() => {
    const raw = (searchParams.get('scene') ?? '').trim();
    return SCENE_PATTERN.test(raw) ? raw : null;
  }, [searchParams]);

  // 正在编辑的行 id（来自 /:id 或 scene 查到的那行）；null = 新录入
  const [rowId, setRowId] = useState<number | null>(pathId);
  const [loading, setLoading] = useState(!!pathId || !!sceneCode);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState(emptyForm);

  // 项目名称下拉候选（project 表）与开合；optionsLoaded 区分「没拉到」和「表里真没有」
  const [projectOptions, setProjectOptions] = useState<ProjectItem[]>([]);
  const [optionsLoaded, setOptionsLoaded] = useState(false);
  const [suggestOpen, setSuggestOpen] = useState(false);

  // 候选只拉一次：模糊匹配在本地做，避免每敲一个字打一次接口；
  // includePending=true：台账里「待定」项目也要能选（后端默认只给已承接项目）；
  // 拉取失败静默降级为纯手输——后端保存时按名查重/补建（_ensure_project_row），
  // 名字已存在不会重复建项，列表漏了也不会建重。
  useEffect(() => {
    getProjects('', 0, 1000, true)
      .then((rows) => { setProjectOptions(rows); setOptionsLoaded(true); })
      .catch(() => setProjectOptions([]));
  }, []);

  /** 项目名称与 project 表里某个项目完全同名（去空格、忽略大小写）→ 就是那个项目 */
  const findExactProject = useCallback((name: string): ProjectItem | null => {
    const kw = name.trim().toLowerCase();
    if (!kw) return null;
    return projectOptions.find((p) => (p.name || '').trim().toLowerCase() === kw) || null;
  }, [projectOptions]);

  const matchedProject = useMemo(() => findExactProject(form.project_name), [findExactProject, form.project_name]);

  // 模糊匹配项目名/编码（本地子串匹配），最多 8 条建议。
  // 名称已经完全等于某个项目时不列候选：此时编号已同步带出，下拉只会挡住下面那行
  const projectSuggestions = useMemo(() => {
    const kw = form.project_name.trim().toLowerCase();
    if (!kw || matchedProject) return [];
    return projectOptions
      .filter((p) =>
        (p.name || '').toLowerCase().includes(kw) || (p.project_code || '').toLowerCase().includes(kw))
      .slice(0, 8);
  }, [form.project_name, projectOptions, matchedProject]);

  // 手输的新名字（project 表里没有同名项目）→ 保存时后端会自动新建，给一句提示；
  // 列表没拉到时（optionsLoaded=false）不提示，避免把已有项目误报成新建
  const isNewProjectName = useMemo(
    () => optionsLoaded && !!form.project_name.trim() && !matchedProject,
    [form.project_name, matchedProject, optionsLoaded],
  );

  /** 选中建议项：项目名称与项目编号一起带出（编号就是它在 project 表的 id/code） */
  const pickProject = useCallback((p: ProjectItem) => {
    setForm((prev) => ({ ...prev, project_name: p.name || '', project_code: p.project_code || '' }));
    setSuggestOpen(false);
  }, []);

  /** 这个编号是不是 project 表里某个项目的编号（即从表里同步来的） */
  const isProjectTableCode = useCallback(
    (code: string) => !!code && projectOptions.some((p) => (p.project_code || '') === code),
    [projectOptions],
  );

  /** 改项目名称：打出来的名字正好是 project 表里的某个项目 → 等同于选中它，项目编号
      立刻从表里同步带出；其余情况 = 新项目，编号若是「表里某个项目的编号」就清掉
      （新项目沿用别人的编号必被后端 400 拦下），用户自己敲的编号不动 */
  const setNameField = (value: unknown) => {
    const name = String(value ?? '');
    const hit = findExactProject(name);
    setForm((prev) => {
      if (hit) return { ...prev, project_name: name, project_code: hit.project_code || '' };
      const kept = isProjectTableCode(prev.project_code) ? '' : prev.project_code;
      return { ...prev, project_name: name, project_code: kept };
    });
  };

  /** 点进项目编号框：名称已匹配到 project 表里的项目，就把表里的编号取过来填上
      （2026-09-30 用户口径）。两个场景要靠它兜：①候选列表比手速慢——名字先打完、
      列表后到，那次输入没同步；②编辑旧录入行时行上的编号与表里不一致，以表为准
      （编号只跟 project 表对名字，后端不会替我们纠正）。 */
  const syncCodeFromMatchedProject = () => {
    const hit = findExactProject(form.project_name);
    if (!hit) return;
    const code = hit.project_code || '';
    setForm((prev) => (prev.project_code === code ? prev : { ...prev, project_code: code }));
  };

  // 名称匹配到表里的项目、编号还空着 → 直接补上，不用等用户去点编号框
  //（列表晚到的那次输入不算「没匹配」，这里认账；已有编号不动，编辑行时不会被覆盖）
  useEffect(() => {
    if (!matchedProject) return;
    const code = matchedProject.project_code || '';
    if (!code) return;
    setForm((prev) => (prev.project_code ? prev : { ...prev, project_code: code }));
  }, [matchedProject]);

  const fillFromRow = useCallback((row: QrcodeItem) => {
    setForm({
      project_code: row.project_code || '',
      project_name: row.project_name || '',
      project_location: row.project_location || '',
      customer_name: row.customer_name || '',
      vehicle_model: row.vehicle_model || '',
    });
    setRowId(row.id);
  }, []);

  // 扫码进入（链接带 scene）且该行已 published：录入信息已保存（保存即发布），不停留本页，
  // 直接去「我要摇人」——scene/openid 原样带过去，CallView 会按 scene 弹车体信息确认
  // （2026-09-30 用户口径）。管理端「编辑信息」链接不带 scene，走不到这里。
  const toCallIfPublished = useCallback((row: QrcodeItem): boolean => {
    if (!sceneCode || row.status !== 'published') return false;
    const qs = searchParams.toString();
    navigate(qs ? `/call?${qs}` : '/call', { replace: true });
    return true;
  }, [sceneCode, navigate, searchParams]);

  // /:id 路由不带 scene 时，pathId 的 fallback 查询不应触发 published 跳转
  // （管理端「编辑信息」按钮就是这个链路，已 published 的行仍然要能编辑）
  const toCallIfPublishedForPathId = useCallback((row: QrcodeItem): boolean => {
    // 只在有 scene 的情况下才考虑跳转；无 scene 说明是管理端直接编辑，不跳
    return false;
  }, []);

  useEffect(() => {
    // 没带 scene 也没带 :id：管理端手动新建，空表单直接可填
    if (!sceneCode && !pathId) return;
    setLoading(true);

    (async () => {
      try {
        // 扫码进入优先按场景值查行（登录即可接口）。scene 即 str(id)：
        // 查到 → 编辑那一行；查不到且链接还带 :id → 退回按 id 查（管理端场景）；
        // 都没有 → 新录入（项目id 保存后自动生成，无需预填）
        if (sceneCode) {
          const row = await fetchQrcodeByScene(sceneCode);
          if (row) {
            if (toCallIfPublished(row)) return;
            fillFromRow(row);
            return;
          }
          if (!pathId) {
            setForm(emptyForm);
            setRowId(null);
            return;
          }
        }
        if (pathId) {
          const row = await fetchQrcode(pathId);
          if (toCallIfPublishedForPathId(row)) return;
          fillFromRow(row);
        }
      } catch (err) {
        Toast({ message: `加载失败：${errMsg(err, '请稍后重试')}`, theme: 'error' });
      } finally {
        setLoading(false);
      }
    })();
  }, [sceneCode, pathId, fillFromRow, toCallIfPublished]);

  const setField = (key: keyof typeof emptyForm) => (value: unknown) =>
    setForm((prev) => ({ ...prev, [key]: String(value ?? '') }));

  const handleSubmit = async () => {
    const code = form.project_code.trim();
    const name = form.project_name.trim();
    const location = form.project_location.trim();
    const customer = form.customer_name.trim();
    const vehicle = form.vehicle_model.trim();
    // 表单顺序：校验也按界面顺序报，用户体验更顺
    if (!name) { Toast({ message: '请填写项目名称', theme: 'warning' }); return; }
    if (!code) { Toast({ message: '请填写项目编号', theme: 'warning' }); return; }
    if (!location) { Toast({ message: '请填写项目地点', theme: 'warning' }); return; }
    if (!customer) { Toast({ message: '请填写客户名称', theme: 'warning' }); return; }
    if (!vehicle) { Toast({ message: '请填写车型', theme: 'warning' }); return; }

    const fields = {
      project_code: code,
      project_name: name,
      project_location: location,
      customer_name: customer,
      vehicle_model: vehicle,
    };

    setSubmitting(true);
    try {
      // 项目id 不入参：编辑时 = 行 id 本身；新建时保存后由后端自动生成（str(id)）
      if (rowId) await updateProjectInfo(rowId, fields);
      else await createProjectInfo(fields);
      Toast({ message: '保存成功', theme: 'success' });
      navigate(-1);
    } catch (err) {
      Toast({ message: `保存失败：${errMsg(err, '请稍后重试')}`, theme: 'error' });
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <Loading text="加载中..." />;

  return (
    <div className="info-entry">
      <h4 className="info-entry__title">{rowId ? '编辑信息' : '录入信息'}</h4>
      {/* 标签统一左对齐、等宽（labelAlign/labelWidth 给在 Form 上，五个字段一起生效）：
          tdesign 默认是 right + 81px，扣掉 16px 内边距只剩 65px，四字标签加上必填星号
          会被挤成两行；96px 后标签一行放下，输入框也随内容区缩进完全等长对齐。
          必填星号放标签右侧（requiredMarkPosition）：放左边会把「项目名称」这类
          带星号的标签整体推右，五个标签的左边缘就对不齐了 */}
      <Form
        className="info-entry__form"
        labelAlign="left"
        labelWidth="96px"
        requiredMarkPosition="right"
        onSubmit={handleSubmit}
      >
        {/* 注意：FormItem 都不能加 name——见文件开头注释。 */}
        <FormItem label="项目名称" requiredMark>
          <div className="proj-suggest">
            <ClearableInput
              value={form.project_name}
              onChange={setNameField}
              onFocus={() => setSuggestOpen(true)}
              onBlur={() => setSuggestOpen(false)}
              placeholder="可匹配已有项目或输入新项目名称"
              maxlength={128}
            />
            {suggestOpen && (projectSuggestions.length > 0 || isNewProjectName) && (
              <div className="proj-suggest__panel">
                {projectSuggestions.map((p) => (
                  <div
                    key={p.project_code || p.name}
                    className="proj-suggest__item"
                    // mousedown 里选：点建议项会先触发 input blur（面板随之隐藏），
                    // click 就点不到了；preventDefault 保住焦点
                    onMouseDown={(e) => { e.preventDefault(); pickProject(p); }}
                  >
                    <div className="proj-suggest__item-name">{p.name}</div>
                    {p.project_code && <div className="proj-suggest__item-code">{p.project_code}</div>}
                  </div>
                ))}
                {isNewProjectName && (
                  <div className="proj-suggest__empty">不在项目表中，保存时将自动新建项目</div>
                )}
              </div>
            )}
          </div>
        </FormItem>
        <FormItem label="项目编号" requiredMark>
          {/* 包一层 div 让 FormItem 的直接子元素不是 ClearableInput：
              tdesign-mobile-react FormItem 对单个子元素会 cloneElement 并注入内部 store，
              覆盖 React 受控 value；包 div 后 FormItem 把 value/onChange 注入 div，
              div 忽略这些 props，内部 ClearableInput 正常用 React state 渲染。
              跨字段联动（pickProject / matchedProject effect 手动 setForm）才能正确回显。 */}
          <div>
            <ClearableInput
              value={form.project_code}
              onChange={setField('project_code')}
              onFocus={syncCodeFromMatchedProject}
              placeholder="请输入项目编号"
              maxlength={64}
            />
          </div>
        </FormItem>
        <FormItem label="项目地点" requiredMark>
          <div>
            <ClearableInput
              value={form.project_location}
              onChange={setField('project_location')}
              placeholder="请输入项目地点"
              maxlength={128}
            />
          </div>
        </FormItem>
        <FormItem label="客户名称" requiredMark>
          <div>
            <ClearableInput
              value={form.customer_name}
              onChange={setField('customer_name')}
              placeholder="请输入客户名称"
              maxlength={128}
            />
          </div>
        </FormItem>
        <FormItem label="车型" requiredMark>
          <div>
            <ClearableInput
              value={form.vehicle_model}
              onChange={setField('vehicle_model')}
              placeholder="请输入车型"
              maxlength={128}
            />
          </div>
        </FormItem>
        <FormItem>
          <Button theme="primary" block type="submit" loading={submitting}>
            保存
          </Button>
        </FormItem>
      </Form>
    </div>
  );
}
