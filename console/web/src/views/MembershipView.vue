<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus as UploadIcon } from '@element-plus/icons-vue'
import { dataApi, configApi, renewalApi, metaApi } from '@/api'
import type { GroupRecord, RenewalCode } from '@/types/api'
import type { UploadFile } from 'element-plus'

function getApiErrorMessage(e: any): string {
  const status = e?.response?.status
  const detail = e?.response?.data?.detail
  if (status === 404) return detail || '群不存在或不在会员库'
  if (status === 503) return detail || '托管 Bot 离线或不可用'
  if (status === 400) return detail || '请求参数错误'
  if (status === 401) return '未授权，检查访问令牌'
  return detail || e?.message || String(e)
}

const loading = ref(false)
const groups = ref<GroupRecord[]>([])
const codes = ref<RenewalCode[]>([])
const bots = ref<string[]>([])
const soonThresholdDays = ref(7)

const searchKey = ref('')
const statusFilter = ref('')
const currentPage = ref(1)
const pageSize = ref(20)

const addDialogVisible = ref(false)
const editDialogVisible = ref(false)
const codeDialogVisible = ref(false)
const notifyDialogVisible = ref(false)
const remarkDialogVisible = ref(false)

const addForm = ref({
  group_id: '',
  length: 30,
  unit: '天',
  expiry: '',
  managed_by_bot: '',
  remark: '',
  renewer: ''
})

const editForm = ref({
  id: undefined as number | undefined,
  group_id: '',
  length: 30,
  unit: '天',
  expiry: '',
  managed_by_bot: '',
  remark: '',
  renewer: ''
})

const codeForm = ref({
  length: 30,
  unit: '天',
  maxUse: 1,
  expireDays: 7
})

const notifyForm = ref({
  text: ''
})
const notifyImages = ref<string[]>([])
const selectedGroups = ref<GroupRecord[]>([])

const remarkForm = ref({
  id: undefined as number | undefined,
  group_id: '',
  expiry: '',
  managed_by_bot: '',
  renewer: '',
  remark: ''
})

const daysRemaining = (expiry: string): number => {
  try {
    const e = new Date(expiry)
    const n = new Date()
    e.setHours(0, 0, 0, 0)
    n.setHours(0, 0, 0, 0)
    return Math.round((e.getTime() - n.getTime()) / 86400000)
  } catch {
    return 0
  }
}

const getStatus = (days: number): GroupRecord['status'] => {
  if (days < 0) return 'expired'
  if (days === 0) return 'today'
  if (days <= soonThresholdDays.value) return 'soon'
  return 'active'
}

const statusText = (status: string) => {
  switch (status) {
    case 'active': return '有效'
    case 'soon': return '即将到期'
    case 'today': return '今日到期'
    case 'expired': return '已过期'
    default: return status
  }
}

const statusClass = (status: string) => {
  return `status-badge status-${status}`
}

const formatExpiry = (expiry: string) => {
  if (!expiry) return '-'
  try {
    const date = new Date(expiry)
    const year = date.getFullYear()
    const month = String(date.getMonth() + 1).padStart(2, '0')
    const day = String(date.getDate()).padStart(2, '0')
    const hours = String(date.getHours()).padStart(2, '0')
    const minutes = String(date.getMinutes()).padStart(2, '0')
    return `${year}/${month}/${day} ${hours}:${minutes}`
  } catch {
    return expiry
  }
}

const filteredGroups = computed(() => {
  let result = groups.value
  if (searchKey.value) {
    const q = searchKey.value.toLowerCase()
    result = result.filter(g => g.gid.toLowerCase().includes(q))
  }
  if (statusFilter.value) {
    result = result.filter(g => g.status === statusFilter.value)
  }
  // 按到期时间排序：越早到期的排在前面
  result = result.sort((a, b) => {
    const dateA = new Date(a.expiry).getTime()
    const dateB = new Date(b.expiry).getTime()
    return dateA - dateB
  })
  return result
})

const pagedGroups = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredGroups.value.slice(start, start + pageSize.value)
})

const totalPages = computed(() => Math.ceil(filteredGroups.value.length / pageSize.value))

const loadData = async () => {
  loading.value = true
  try {
    const [data, codesData, config, botsData] = await Promise.all([
      dataApi.getAll(),
      renewalApi.getCodes().catch(() => ({ generatedCodes: [] })),
      configApi.getAll().catch(() => ({})),
      metaApi.getBots().catch(() => ({ bots: [] }))
    ])

    const noticeDays = config?.membership?.expire_notice_days
    if (Array.isArray(noticeDays) && noticeDays.length) {
      soonThresholdDays.value = Math.max(...noticeDays.map(Number).filter(Number.isFinite))
    }

    groups.value = Object.entries(data)
      .filter(([k, v]) => k !== 'generatedCodes' && typeof v === 'object')
      .map(([gid, info]: [string, any]) => {
        const days = daysRemaining(info.expiry)
        return {
          id: info.id,
          gid,
          expiry: info.expiry,
          days,
          status: getStatus(days),
          managed_by_bot: info.managed_by_bot,
          last_renewed_by: info.last_renewed_by,
          remark: info.remark
        }
      })

    // 将续费码对象转换为数组
    const codesObj = (codesData as any) || {}
    codes.value = Object.entries(codesObj).map(([code, info]: [string, any]) => ({
      code,
      length: info.length,
      unit: info.unit,
      generated_time: info.generated_time,
      max_use: info.max_use,
      used_count: info.used_count,
      expire_at: info.expire_at
    }))

    bots.value = botsData.bots || []
  } catch (e: any) {
    ElMessage.error('加载数据失败: ' + (e?.message || e))
  } finally {
    loading.value = false
  }
}

const getYesterdayStr = (): string => {
  const date = new Date()
  date.setDate(date.getDate() - 1)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const addToDate = (dateStr: string, length: number, unit: string): Date => {
  const date = new Date(dateStr)
  // Fix timezone offset for date-only strings to ensure consistent calculation
  if (dateStr.length === 10) {
    date.setHours(0, 0, 0, 0)
  }
  
  if (unit === '天') {
    date.setDate(date.getDate() + length)
  } else if (unit === '月') {
    date.setMonth(date.getMonth() + length)
  } else if (unit === '年') {
    date.setFullYear(date.getFullYear() + length)
  }
  return date
}

const openAddDialog = () => {
  addForm.value = {
    group_id: '',
    length: 30,
    unit: '天',
    expiry: getYesterdayStr(),
    managed_by_bot: '',
    remark: '',
    renewer: ''
  }
  addDialogVisible.value = true
}

const openEditDialog = (row: GroupRecord) => {
  editForm.value = {
    id: row.id,
    group_id: row.gid,
    length: 0,
    unit: '天',
    expiry: row.expiry || getYesterdayStr(),
    managed_by_bot: row.managed_by_bot || '',
    remark: row.remark || '',
    renewer: ''
  }
  editDialogVisible.value = true
}

const submitAdd = async () => {
  if (!addForm.value.group_id.trim()) {
    ElMessage.warning('请输入群号')
    return
  }
  if (!addForm.value.managed_by_bot) {
    ElMessage.warning('请选择管理Bot')
    return
  }
  if (!addForm.value.renewer) {
    ElMessage.warning('请填写续费人')
    return
  }

  // Local calculation
  const finalDate = addToDate(addForm.value.expiry, addForm.value.length, addForm.value.unit)
  const today = new Date()
  today.setHours(0, 0, 0, 0)

  if (finalDate < today) {
    ElMessage.warning('计算后的到期时间已过期，无法新增')
    return
  }

  try {
    const payload: Record<string, any> = {
      group_id: addForm.value.group_id.trim(),
      managed_by_bot: addForm.value.managed_by_bot,
      renewer: addForm.value.renewer,
      expiry: finalDate.toISOString()
    }
    if (addForm.value.remark) payload.remark = addForm.value.remark

    await renewalApi.extend(payload)
    ElMessage.success('添加成功')
    addDialogVisible.value = false
    loadData()
  } catch (e: any) {
    const msg = e?.response?.data?.detail || e?.message || e
    ElMessage.error('添加失败: ' + msg)
  }
}

const submitEdit = async () => {
  if (!editForm.value.group_id.trim()) {
    ElMessage.warning('请输入群号')
    return
  }

  // Local calculation
  const finalDate = addToDate(editForm.value.expiry, editForm.value.length, editForm.value.unit)
  const today = new Date()
  today.setHours(0, 0, 0, 0)

  // Auto-delete if expired
  if (finalDate < today) {
    try {
      await ElMessageBox.confirm(
        `计算后的到期时间 (${finalDate.toLocaleDateString()}) 已过期。\n确认将自动删除该群记录并退群?`,
        '过期自动清理',
        { type: 'warning' }
      )
      await renewalApi.leave(parseInt(editForm.value.group_id.trim()))
      ElMessage.success('已过期，记录已删除')
      editDialogVisible.value = false
      loadData()
    } catch (e) {
      if (e !== 'cancel' && e !== 'close') {
         ElMessage.error('清理失败: ' + e)
      }
    }
    return
  }

  try {
    const payload: Record<string, any> = {
      id: editForm.value.id,
      group_id: editForm.value.group_id.trim(),
      expiry: finalDate.toISOString()
    }

    if (editForm.value.renewer) payload.renewer = editForm.value.renewer
    if (editForm.value.managed_by_bot) payload.managed_by_bot = editForm.value.managed_by_bot
    if (editForm.value.remark) payload.remark = editForm.value.remark

    await renewalApi.extend(payload)
    ElMessage.success('保存成功')
    editDialogVisible.value = false
    loadData()
  } catch (e: any) {
    const msg = e?.response?.data?.detail || e?.message || e
    ElMessage.error('保存失败: ' + msg)
  }
}

const handleRemind = async (row: GroupRecord) => {
  try {
    await ElMessageBox.confirm('确认向该群发送续费提醒?', '提醒')
    await renewalApi.remind(parseInt(row.gid))
    ElMessage.success('提醒已发送')
  } catch (e: any) {
    if (e === 'cancel' || e === 'close') return
    const msg = getApiErrorMessage(e)
    ElMessageBox.alert(msg, '发送提醒失败', { type: 'error' })
  }
}

const handleLeave = async (row: GroupRecord) => {
  try {
    await ElMessageBox.confirm('确认要让机器人退出该群?', '退群', { type: 'warning' })
    await renewalApi.leave(parseInt(row.gid))
    ElMessage.success('已退群')
    loadData()
  } catch (e: any) {
    if (e === 'cancel' || e === 'close') return
    const msg = getApiErrorMessage(e)
    ElMessageBox.alert(msg, '退群失败', { type: 'error' })
  }
}

const generateCode = async () => {
  try {
    const res = await renewalApi.generateCode(
      codeForm.value.length,
      codeForm.value.unit,
      codeForm.value.maxUse,
      codeForm.value.expireDays
    )
    ElMessage.success('生成成功: ' + res.code)
    codeDialogVisible.value = false
    loadData()
  } catch (e: any) {
    ElMessage.error('生成失败: ' + (e?.message || e))
  }
}

const copyCode = async (code: string) => {
  try {
    // 第一层:尝试使用现代 Clipboard API
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(code)
      ElMessage.success('已复制')
      return
    }
  } catch (error) {
    console.warn('Clipboard API 失败,尝试fallback方法:', error)
  }

  // 第二层:使用传统的 execCommand 方法(兼容性更好)
  try {
    const textarea = document.createElement('textarea')
    textarea.value = code
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    textarea.style.left = '-9999px'
    document.body.appendChild(textarea)
    textarea.focus()
    textarea.select()
    const successful = document.execCommand('copy')
    document.body.removeChild(textarea)

    if (successful) {
      ElMessage.success('已复制')
    } else {
      throw new Error('execCommand 返回 false')
    }
  } catch (error) {
    console.error('复制失败:', error)
    ElMessage.error('复制失败,请手动复制')
  }
}

const openNotifyDialog = () => {
  if (selectedGroups.value.length === 0) {
    ElMessage.warning('请先选择要通知的群组')
    return
  }
  notifyForm.value = { text: '' }
  notifyImages.value = []
  notifyDialogVisible.value = true
}

const handleImageUpload = (file: UploadFile) => {
  const reader = new FileReader()
  reader.onload = (e) => {
    if (e.target?.result) {
      notifyImages.value.push(e.target.result as string)
    }
  }
  if (file.raw) {
    reader.readAsDataURL(file.raw)
  }
  return false
}

const removeImage = (index: number) => {
  notifyImages.value.splice(index, 1)
}

const sendNotify = async () => {
  if (!notifyForm.value.text && !notifyImages.value.length) {
    ElMessage.warning('请输入通知内容或上传图片')
    return
  }
  try {
    const gids = selectedGroups.value.map(g => parseInt(g.gid))
    await renewalApi.notify(gids, notifyForm.value.text, notifyImages.value.length ? notifyImages.value : undefined)
    ElMessage.success('后台发送任务已启动')
    notifyDialogVisible.value = false
  } catch (e: any) {
    ElMessage.error('发送失败: ' + (e?.message || e))
  }
}

const openRemarkDialog = (row: GroupRecord) => {
  remarkForm.value = {
    id: row.id,
    group_id: row.gid,
    expiry: row.expiry || '',
    managed_by_bot: row.managed_by_bot || '',
    renewer: row.last_renewed_by || '',
    remark: row.remark || ''
  }
  remarkDialogVisible.value = true
}

const submitRemark = async () => {
  try {
    const payload: Record<string, any> = {
      id: remarkForm.value.id,
      group_id: remarkForm.value.group_id,
      expiry: remarkForm.value.expiry,
      remark: remarkForm.value.remark
    }

    // 可选字段
    if (remarkForm.value.managed_by_bot) {
      payload.managed_by_bot = remarkForm.value.managed_by_bot
    }
    if (remarkForm.value.renewer) {
      payload.renewed_by = remarkForm.value.renewer
    }

    await renewalApi.extend(payload)
    ElMessage.success('备注保存成功')
    remarkDialogVisible.value = false
    loadData()
  } catch (e: any) {
    const msg = e?.response?.data?.detail || e?.message || e
    ElMessage.error('保存失败: ' + msg)
  }
}

onMounted(() => {
  loadData()
})
</script>

<template>
  <div v-loading="loading" class="membership-view">
    <div class="panel">
      <div class="panel-header">
        <div class="toolbar" style="display: flex; justify-content: space-between; align-items: center;">
          <div style="display: flex; gap: 10px;">
            <button class="btn btn-secondary btn-sm" @click="openNotifyDialog">通知</button>
            <button class="btn btn-primary btn-sm" @click="openAddDialog">新增</button>
          </div>
          <div style="display: flex; gap: 10px;">
            <input v-model="searchKey" class="input" placeholder="搜索群号...">
            <select v-model="statusFilter" class="input">
              <option value="">全部状态</option>
              <option value="active">有效</option>
              <option value="soon">即将到期</option>
              <option value="today">今日到期</option>
              <option value="expired">已过期</option>
            </select>
          </div>
        </div>
      </div>
      <div class="table-container">
        <table class="data-table">
          <thead>
            <tr>
              <th><input type="checkbox" @change="(e:any) => { if(e.target.checked) { selectedGroups = [...pagedGroups] } else { selectedGroups = [] } }"></th>
              <th>群号</th>
              <th>管理Bot</th>
              <th>状态</th>
              <th>到期时间</th>
              <th>剩余天数</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="pagedGroups.length === 0">
              <td colspan="7" class="text-center">暂无数据</td>
            </tr>
            <tr v-for="row in pagedGroups" :key="row.gid">
              <td><input type="checkbox" :checked="selectedGroups.some(g => g.gid === row.gid)" @change="(e:any) => { if(e.target.checked) { selectedGroups.push(row) } else { selectedGroups = selectedGroups.filter(g => g.gid !== row.gid) } }"></td>
              <td>{{ row.gid }}</td>
              <td>{{ row.managed_by_bot || '-' }}</td>
              <td><span :class="statusClass(row.status)">{{ statusText(row.status) }}</span></td>
              <td>{{ formatExpiry(row.expiry) }}</td>
              <td>{{ row.days }}</td>
              <td>
                <button class="btn-action btn-extend" @click="openEditDialog(row)">编辑</button>
                <button class="btn-action btn-remark" @click="openRemarkDialog(row)">备注</button>
                <button class="btn-action btn-remind" @click="handleRemind(row)">提醒</button>
                <button class="btn-action btn-leave" @click="handleLeave(row)">退群</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div class="pagination-container">
          <div class="pagination-info">
            <span>共 {{ filteredGroups.length }} 条记录</span>
          </div>
          <div class="pagination-controls">
            <button class="pagination-btn" :disabled="currentPage === 1" @click="currentPage = 1">⏮</button>
            <button class="pagination-btn" :disabled="currentPage === 1" @click="currentPage--">◀</button>
            <span class="pagination-pages">{{ currentPage }} / {{ totalPages || 1 }}</span>
            <button class="pagination-btn" :disabled="currentPage >= totalPages" @click="currentPage++">▶</button>
            <button class="pagination-btn" :disabled="currentPage >= totalPages" @click="currentPage = totalPages">⏭</button>
          </div>
          <div class="pagination-size">
            <label>
              <span>每页</span>
              <select v-model="pageSize" class="pagination-size-select" @change="currentPage = 1">
                <option :value="10">10</option>
                <option :value="20">20</option>
                <option :value="50">50</option>
                <option :value="100">100</option>
              </select>
              <span>条</span>
            </label>
          </div>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-header">
        <h3 class="panel-title">续费码管理</h3>
      </div>
      <div class="panel-body">
        <div class="renewal-generate-section">
          <h4 class="section-subtitle">生成新续费码</h4>
          <div class="toolbar">
            <label>
              <span>时长</span>
              <input v-model.number="codeForm.length" type="number" min="1" class="form-input-sm">
            </label>
            <label>
              <span>单位</span>
              <select v-model="codeForm.unit" class="form-select-sm">
                <option value="天">天</option>
                <option value="月">月</option>
                <option value="年">年</option>
              </select>
            </label>
            <button class="btn btn-primary btn-sm" @click="generateCode">生成</button>
          </div>
        </div>

        <div class="divider"></div>

        <div class="renewal-codes-section">
          <h4 class="section-subtitle">待使用的续费码</h4>
          <div class="codes-grid">
            <div v-if="codes.length === 0" class="empty-state">暂无待使用的续费码</div>
            <div v-for="c in codes" :key="c.code" class="code-card">
              <div class="code-info">
                <div class="code-value">{{ c.code }}</div>
                <div class="code-meta">{{ c.length }}{{ c.unit }}</div>
              </div>
              <button class="btn-copy" @click="copyCode(c.code)">复制</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 新增弹窗 -->
    <el-dialog v-model="addDialogVisible" title="新增群组" width="500">
      <el-form :model="addForm" label-width="100px">
        <el-form-item label="群号" required>
          <el-input v-model="addForm.group_id" placeholder="输入群号（必填）" />
        </el-form-item>
        <el-form-item label="到期时间">
          <el-date-picker v-model="addForm.expiry" type="date" placeholder="到期时间" value-format="YYYY-MM-DD" style="width: 100%" />
          <div style="font-size: 12px; color: var(--color-text-tertiary); margin-top: 4px;">格式: YYYY-MM-DD，新增时与续费时长二选一必填</div>
        </el-form-item>
        <el-form-item label="续费时长">
          <div style="display: flex; gap: 8px; width: 100%;">
            <el-input-number v-model="addForm.length" :min="0" :max="9999" placeholder="0表示不续费" style="flex: 1;" />
            <el-select v-model="addForm.unit" style="width: 100px;">
              <el-option label="天" value="天" />
              <el-option label="月" value="月" />
              <el-option label="年" value="年" />
            </el-select>
          </div>
          <div style="font-size: 12px; color: var(--color-text-tertiary); margin-top: 4px;">快捷操作，新增时与到期时间二选一必填</div>
        </el-form-item>
        <el-form-item label="管理Bot" required>
          <el-select v-model="addForm.managed_by_bot" placeholder="请输入要使用的Bot自编号" style="width: 100%" allow-create filterable>
            <el-option v-for="b in bots" :key="b" :label="b" :value="b" />
          </el-select>
        </el-form-item>
        <el-form-item label="续费人" required>
          <el-input v-model="addForm.renewer" placeholder="填写操作人/续费人" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="addForm.remark" placeholder="备注信息（可选）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitAdd">保存</el-button>
      </template>
    </el-dialog>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="editDialogVisible" title="编辑群组" width="500">
      <el-form :model="editForm" label-width="100px">
        <el-form-item label="群号">
          <el-input v-model="editForm.group_id" placeholder="输入群号" />
        </el-form-item>
        <el-form-item label="到期时间">
          <el-date-picker v-model="editForm.expiry" type="date" placeholder="到期时间" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="续费时长">
          <div style="display: flex; gap: 8px; width: 100%;">
            <el-input-number v-model="editForm.length" :min="0" :max="9999" placeholder="0表示不续费" style="flex: 1;" />
            <el-select v-model="editForm.unit" style="width: 100px;">
              <el-option label="天" value="天" />
              <el-option label="月" value="月" />
              <el-option label="年" value="年" />
            </el-select>
          </div>
        </el-form-item>
        <el-form-item label="管理Bot">
          <el-select v-model="editForm.managed_by_bot" placeholder="选择Bot" clearable style="width: 100%" allow-create filterable>
            <el-option v-for="b in bots" :key="b" :label="b" :value="b" />
          </el-select>
        </el-form-item>
        <el-form-item label="续费人">
          <el-input v-model="editForm.renewer" placeholder="填写操作人/续费人" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="editForm.remark" placeholder="备注信息" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 批量通知弹窗 -->
    <el-dialog v-model="notifyDialogVisible" title="发送通知" width="500">
      <el-alert
        :title="`已选择 ${selectedGroups.length} 个群`"
        type="info"
        :closable="false"
        style="margin-bottom: 20px;"
      />
      <el-form :model="notifyForm" label-width="80px">
        <el-form-item label="消息文本">
          <el-input v-model="notifyForm.text" type="textarea" :rows="6" placeholder="输入要发送的消息内容..." />
        </el-form-item>
        <el-form-item label="图片附件">
          <div class="image-upload-area">
            <el-upload
              :auto-upload="false"
              :show-file-list="false"
              accept="image/*"
              multiple
              :on-change="handleImageUpload"
            >
              <el-button type="primary" plain>
                <el-icon><UploadIcon /></el-icon>
                上传图片
              </el-button>
            </el-upload>
            <div v-if="notifyImages.length" class="image-preview-list">
              <div v-for="(img, idx) in notifyImages" :key="idx" class="image-preview-item">
                <img :src="img" alt="preview" />
                <el-button type="danger" size="small" circle @click="removeImage(idx)">
                  <el-icon><Close /></el-icon>
                </el-button>
              </div>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="notifyDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="sendNotify">发送</el-button>
      </template>
    </el-dialog>

    <!-- 备注弹窗 -->
    <el-dialog v-model="remarkDialogVisible" title="查看/修改备注" width="500">
      <el-form :model="remarkForm" label-width="80px">
        <el-form-item label="群号">
          <el-input v-model="remarkForm.group_id" disabled />
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="remarkForm.remark"
            type="textarea"
            :rows="6"
            placeholder="输入备注信息..."
            maxlength="500"
            show-word-limit
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="remarkDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitRemark">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.membership-view {
  padding: 24px;
}

.panel {
  background: var(--color-bg-card);
  backdrop-filter: blur(10px);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 24px;
  margin-bottom: 20px;
  box-shadow: var(--shadow-sm);
  transition: var(--transition);

  &:hover {
    box-shadow: var(--shadow-md);
  }
}

.panel-header {
  margin-bottom: 20px;
}

.panel-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--color-text);
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;

  label {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--color-text-secondary);
    font-size: 14px;
    font-weight: 500;
  }
}

.input {
  padding: 10px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-tertiary);
  color: var(--color-text);
  font-size: 13px;
  transition: var(--transition);

  &:focus {
    outline: none;
    border-color: var(--color-primary);
    box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
  }
}

.form-input-sm {
  width: 100px;
  padding: 7px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-tertiary);
  color: var(--color-text);
  font-size: 13px;
}

.form-select-sm {
  padding: 7px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-tertiary);
  color: var(--color-text);
  font-size: 13px;
  cursor: pointer;
}

.btn {
  padding: 11px 24px;
  border: none;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  transition: var(--transition);
  display: inline-flex;
  align-items: center;
  gap: 8px;

  &:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-lg);
  }
}

.btn-primary {
  background: linear-gradient(135deg, var(--color-primary), var(--color-primary-light));
  color: white;
  box-shadow: var(--shadow-sm);
}

.btn-secondary {
  background: linear-gradient(135deg, var(--color-info), #0891b2);
  color: white;
}

.btn-sm {
  padding: 7px 14px;
  font-size: 13px;
}

.table-container {
  overflow-x: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
}

.data-table {
  width: 100%;
  border-collapse: collapse;

  thead {
    background: linear-gradient(135deg, var(--color-bg-tertiary), var(--color-border-light));
  }

  th, td {
    padding: 12px 16px;
    text-align: left;
    border-bottom: 1px solid var(--color-border);
    transition: var(--transition-fast);
  }

  th {
    font-weight: 700;
    color: var(--color-text);
    font-size: 14px;
    white-space: nowrap;
  }

  td {
    color: var(--color-text-secondary);
    font-size: 13px;
  }

  tbody tr {
    transition: var(--transition-fast);

    &:hover {
      background: var(--color-bg-tertiary);
    }
  }
}

.text-center {
  text-align: center !important;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  padding: 5px 14px;
  border-radius: 16px;
  font-size: 12px;
  font-weight: 700;

  &.status-active {
    background: rgba(16, 185, 129, 0.15);
    color: var(--color-success);
    border: 1px solid rgba(16, 185, 129, 0.3);
  }

  &.status-soon {
    background: rgba(245, 158, 11, 0.15);
    color: var(--color-warning);
    border: 1px solid rgba(245, 158, 11, 0.3);
  }

  &.status-today {
    background: rgba(245, 158, 11, 0.15);
    color: var(--color-warning);
    border: 1px solid rgba(245, 158, 11, 0.3);
  }

  &.status-expired {
    background: rgba(239, 68, 68, 0.15);
    color: var(--color-danger);
    border: 1px solid rgba(239, 68, 68, 0.3);
  }
}

.btn-action {
  padding: 7px 12px;
  border: none;
  border-radius: var(--radius-md);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: var(--transition);
  margin: 0 3px;
  color: white;

  &:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-md);
  }

  &.btn-remind {
    background: var(--color-info);
  }

  &.btn-remark {
    background: #8b5cf6;
  }

  &.btn-extend {
    background: var(--color-success);
  }

  &.btn-leave {
    background: var(--color-danger);
  }
}

.pagination-container {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 15px;
  background: var(--color-bg-tertiary);
  border-top: 1px solid var(--color-border);
  gap: 16px;
  flex-wrap: wrap;
}

.pagination-info {
  font-size: 14px;
  color: var(--color-text-secondary);
  font-weight: 500;
}

.pagination-controls {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pagination-btn {
  width: 32px;
  height: 32px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-card);
  color: var(--color-text);
  font-size: 12px;
  cursor: pointer;
  transition: var(--transition);

  &:hover:not(:disabled) {
    background: var(--color-primary);
    color: white;
    border-color: var(--color-primary);
  }

  &:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
}

.pagination-pages {
  padding: 0 12px;
  font-size: 14px;
  color: var(--color-text);
}

.pagination-size {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--color-text-secondary);
}

.pagination-size-select {
  padding: 6px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-card);
  color: var(--color-text);
  font-size: 13px;
  cursor: pointer;
}

.panel-body {
  margin-top: 16px;
}

.section-subtitle {
  font-size: 15px;
  font-weight: 700;
  color: var(--color-text);
  margin-bottom: 16px;
}

.divider {
  height: 1px;
  background: var(--color-border);
  margin: 24px 0;
}

.codes-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.code-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  transition: var(--transition);

  &:hover {
    transform: translateY(-3px);
    box-shadow: var(--shadow-lg);
    border-color: var(--color-primary);
  }
}

.code-info {
  flex: 1;
}

.code-value {
  font-family: 'Courier New', monospace;
  font-size: 16px;
  font-weight: 700;
  color: var(--color-text);
  margin-bottom: 8px;
  letter-spacing: 2px;
}

.code-meta {
  font-size: 12px;
  color: var(--color-text-tertiary);
  font-weight: 500;
}

.btn-copy {
  padding: 8px 16px;
  background: linear-gradient(135deg, var(--color-primary), var(--color-primary-light));
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: var(--transition);

  &:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-md);
  }
}

.empty-state {
  padding: 60px 20px;
  text-align: center;
  color: var(--color-text-tertiary);
  font-size: 14px;
}

.image-upload-area {
  width: 100%;
}

.image-preview-list {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 12px;
}

.image-preview-item {
  position: relative;
  width: 80px;
  height: 80px;
  border-radius: var(--radius-md);
  overflow: hidden;
  border: 1px solid var(--color-border);

  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .el-button {
    position: absolute;
    top: 4px;
    right: 4px;
    width: 20px;
    height: 20px;
    padding: 0;
  }
}
</style>
