<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { permissionApi, metaApi } from '@/api'
import type { PermissionConfig, LayerConfig } from '@/types/api'

type AccountList = NonNullable<LayerConfig['whitelist']>
type ListSection = 'whitelist' | 'blacklist'
type ListType = keyof AccountList
type InputValue = string | number

const loading = ref(false)
const saving = ref(false)
const permissions = ref<PermissionConfig | null>(null)
const pluginNames = ref<Record<string, string>>({})
const commandNames = ref<Record<string, Record<string, string>>>({})
const expandedPlugins = ref<string[]>([])
const hasChanges = ref(false)
const listDrafts = ref<Record<string, string>>({})

const jsonDialogVisible = ref(false)
const jsonContent = ref('')

const levelOptions = [
  { label: '所有人', value: 'all' },
  { label: '群成员', value: 'member' },
  { label: '管理员', value: 'admin' },
  { label: '群主', value: 'owner' },
  { label: 'Bot管理', value: 'bot_admin' },
  { label: '超级用户', value: 'superuser' }
]

const sceneOptions = [
  { label: '全部', value: 'all' },
  { label: '仅群聊', value: 'group' },
  { label: '仅私聊', value: 'private' }
]

const getSceneLabel = (value: string) => {
  return sceneOptions.find(o => o.value === value)?.label || value
}

const getLevelLabel = (value: string) => {
  return levelOptions.find(o => o.value === value)?.label || value
}

const loadData = async () => {
  loading.value = true
  try {
    const [perm, plugins, commands] = await Promise.all([
      permissionApi.get(),
      metaApi.getPlugins().catch(() => ({})),
      metaApi.getCommands().catch(() => ({}))
    ])
    permissions.value = perm
    pluginNames.value = plugins
    commandNames.value = commands
    listDrafts.value = {}
    hasChanges.value = false
  } catch (e: any) {
    ElMessage.error('加载权限失败: ' + (e?.message || e))
  } finally {
    loading.value = false
  }
}

const savePermissions = async () => {
  if (!permissions.value) return
  saving.value = true
  try {
    await permissionApi.save(permissions.value)
    ElMessage.success('保存成功')
    listDrafts.value = {}
    hasChanges.value = false
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (e?.message || e))
  } finally {
    saving.value = false
  }
}

const togglePlugin = (pluginId: string) => {
  const idx = expandedPlugins.value.indexOf(pluginId)
  if (idx >= 0) {
    expandedPlugins.value.splice(idx, 1)
  } else {
    expandedPlugins.value.push(pluginId)
  }
}

const isExpanded = (pluginId: string) => expandedPlugins.value.includes(pluginId)

const getPluginName = (key: string) => pluginNames.value[key] || key
const getCommandName = (plugin: string, cmd: string) => commandNames.value[plugin]?.[cmd] || cmd

const parseList = (value: InputValue): string[] => {
  return String(value).split(/[,，\s]+/).map(s => s.trim()).filter(Boolean)
}

const formatList = (arr: string[] | undefined): string => {
  return arr?.join(', ') || ''
}

const listInputKey = (...parts: Array<string | number>) => {
  return parts.map(part => String(part)).join(':')
}

const getListInputValue = (key: string, arr: string[] | undefined): string => {
  return listDrafts.value[key] ?? formatList(arr)
}

const ensureAccountList = (layer: LayerConfig, section: ListSection): AccountList => {
  if (!layer[section]) {
    layer[section] = { users: [], groups: [] }
  }
  return layer[section]
}

const updateAccountList = (
  key: string,
  layer: LayerConfig,
  section: ListSection,
  type: ListType,
  val: InputValue
) => {
  listDrafts.value[key] = String(val)
  ensureAccountList(layer, section)[type] = parseList(val)
  hasChanges.value = true
}

const updateWhitelist = (key: string, layer: LayerConfig, type: ListType, val: InputValue) => {
  updateAccountList(key, layer, 'whitelist', type, val)
}

const updateBlacklist = (key: string, layer: LayerConfig, type: ListType, val: InputValue) => {
  updateAccountList(key, layer, 'blacklist', type, val)
}

const markChanged = () => {
  hasChanges.value = true
}

const openJsonDialog = () => {
  if (permissions.value) {
    jsonContent.value = JSON.stringify(permissions.value, null, 2)
    jsonDialogVisible.value = true
  }
}

const saveJson = () => {
  try {
    const parsed = JSON.parse(jsonContent.value)
    permissions.value = parsed
    listDrafts.value = {}
    hasChanges.value = true
    jsonDialogVisible.value = false
    ElMessage.success('JSON已更新，请点击保存配置按钮保存')
  } catch (e: any) {
    ElMessage.error('JSON格式错误: ' + e.message)
  }
}

onMounted(() => {
  loadData()
})
</script>

<template>
  <div v-loading="loading" class="permissions-view">
    <div class="panel">
      <div class="panel-header">
        <h3 class="panel-title">权限配置</h3>
        <div class="toolbar-right">
          <button class="btn btn-secondary btn-sm" @click="openJsonDialog">编辑JSON</button>
          <button class="btn btn-primary btn-sm" :disabled="!hasChanges" @click="savePermissions">保存配置</button>
        </div>
      </div>

      <div v-if="!permissions" class="empty-state">暂无权限配置</div>

      <div v-else class="perm-accordion">
      <!-- 全局配置 -->
      <el-card shadow="never" class="perm-card global-card">
        <template #header>
          <div class="plugin-header">
            <span>🌐 全局权限配置</span>
            <el-switch v-model="permissions.top.enabled" @change="markChanged" style="margin-left: auto;" />
          </div>
        </template>
        <div class="layer-config">
          <div class="config-row">
            <label>💬 场景</label>
            <el-popover placement="bottom" :width="200" trigger="click">
              <template #reference>
                <el-button class="select-display-btn">
                  {{ getSceneLabel(permissions.top.scene) }}
                </el-button>
              </template>
              <div class="popover-options">
                <div
                  v-for="o in sceneOptions"
                  :key="o.value"
                  class="option-item"
                  :class="{ active: permissions.top.scene === o.value }"
                  @click="permissions.top.scene = o.value; markChanged()"
                >
                  {{ o.label }}
                </div>
              </div>
            </el-popover>
          </div>
          <div class="config-row">
            <label>👤 权限等级</label>
            <el-popover placement="bottom" :width="200" trigger="click">
              <template #reference>
                <el-button class="select-display-btn">
                  {{ getLevelLabel(permissions.top.level) }}
                </el-button>
              </template>
              <div class="popover-options">
                <div
                  v-for="o in levelOptions"
                  :key="o.value"
                  class="option-item"
                  :class="{ active: permissions.top.level === o.value }"
                  @click="permissions.top.level = o.value; markChanged()"
                >
                  {{ o.label }}
                </div>
              </div>
            </el-popover>
          </div>
        </div>
        <div class="list-config">
          <div class="list-group">
            <label>✅ 用户白名单</label>
            <el-input
              :model-value="getListInputValue(listInputKey('top', 'whitelist', 'users'), permissions.top.whitelist?.users)"
              @update:model-value="v => updateWhitelist(listInputKey('top', 'whitelist', 'users'), permissions.top, 'users', v)"
              placeholder="逗号分隔的用户ID"
            />
          </div>
          <div class="list-group">
            <label>✅ 群白名单</label>
            <el-input
              :model-value="getListInputValue(listInputKey('top', 'whitelist', 'groups'), permissions.top.whitelist?.groups)"
              @update:model-value="v => updateWhitelist(listInputKey('top', 'whitelist', 'groups'), permissions.top, 'groups', v)"
              placeholder="逗号分隔的群号"
            />
          </div>
          <div class="list-group">
            <label>⛔ 用户黑名单</label>
            <el-input
              :model-value="getListInputValue(listInputKey('top', 'blacklist', 'users'), permissions.top.blacklist?.users)"
              @update:model-value="v => updateBlacklist(listInputKey('top', 'blacklist', 'users'), permissions.top, 'users', v)"
              placeholder="逗号分隔的用户ID"
            />
          </div>
          <div class="list-group">
            <label>⛔ 群黑名单</label>
            <el-input
              :model-value="getListInputValue(listInputKey('top', 'blacklist', 'groups'), permissions.top.blacklist?.groups)"
              @update:model-value="v => updateBlacklist(listInputKey('top', 'blacklist', 'groups'), permissions.top, 'groups', v)"
              placeholder="逗号分隔的群号"
            />
          </div>
        </div>
      </el-card>

      <!-- 子插件配置 -->
      <el-card
        v-for="(plugin, pluginId) in permissions.sub_plugins"
        :key="pluginId"
        shadow="never"
        class="perm-card"
      >
        <template #header>
          <div
            class="plugin-header"
            :class="{ active: isExpanded(pluginId) }"
            @click="togglePlugin(pluginId)"
          >
            <span class="expand-icon">▶️</span>
            <span class="plugin-name">🔌 {{ getPluginName(pluginId) }}</span>
            <span class="plugin-id">({{ pluginId }})</span>
            <el-switch
              v-model="plugin.top.enabled"
              @change="markChanged"
              @click.stop
              class="enable-switch"
              size="small"
            />
          </div>
        </template>

        <el-collapse-transition>
          <div v-if="isExpanded(pluginId)" class="plugin-body">
            <!-- 插件顶级配置 -->
            <div class="layer-section">
              <div class="section-title">插件配置</div>
              <div class="layer-config">
                <div class="config-row">
                  <label>💬 场景</label>
                  <el-popover placement="bottom" :width="200" trigger="click">
                    <template #reference>
                      <el-button class="select-display-btn">
                        {{ getSceneLabel(plugin.top.scene) }}
                      </el-button>
                    </template>
                    <div class="popover-options">
                      <div
                        v-for="o in sceneOptions"
                        :key="o.value"
                        class="option-item"
                        :class="{ active: plugin.top.scene === o.value }"
                        @click="plugin.top.scene = o.value; markChanged()"
                      >
                        {{ o.label }}
                      </div>
                    </div>
                  </el-popover>
                </div>
                <div class="config-row">
                  <label>👤 权限等级</label>
                  <el-popover placement="bottom" :width="200" trigger="click">
                    <template #reference>
                      <el-button class="select-display-btn">
                        {{ getLevelLabel(plugin.top.level) }}
                      </el-button>
                    </template>
                    <div class="popover-options">
                      <div
                        v-for="o in levelOptions"
                        :key="o.value"
                        class="option-item"
                        :class="{ active: plugin.top.level === o.value }"
                        @click="plugin.top.level = o.value; markChanged()"
                      >
                        {{ o.label }}
                      </div>
                    </div>
                  </el-popover>
                </div>
              </div>
              <div class="list-config">
                <div class="list-group">
                  <label>✅ 用户白名单</label>
                  <el-input
                    :model-value="getListInputValue(listInputKey('plugin', pluginId, 'top', 'whitelist', 'users'), plugin.top.whitelist?.users)"
                    @update:model-value="v => updateWhitelist(listInputKey('plugin', pluginId, 'top', 'whitelist', 'users'), plugin.top, 'users', v)"
                    placeholder="逗号分隔"
                  />
                </div>
                <div class="list-group">
                  <label>✅ 群白名单</label>
                  <el-input
                    :model-value="getListInputValue(listInputKey('plugin', pluginId, 'top', 'whitelist', 'groups'), plugin.top.whitelist?.groups)"
                    @update:model-value="v => updateWhitelist(listInputKey('plugin', pluginId, 'top', 'whitelist', 'groups'), plugin.top, 'groups', v)"
                    placeholder="逗号分隔"
                  />
                </div>
                <div class="list-group">
                  <label>⛔ 用户黑名单</label>
                  <el-input
                    :model-value="getListInputValue(listInputKey('plugin', pluginId, 'top', 'blacklist', 'users'), plugin.top.blacklist?.users)"
                    @update:model-value="v => updateBlacklist(listInputKey('plugin', pluginId, 'top', 'blacklist', 'users'), plugin.top, 'users', v)"
                    placeholder="逗号分隔"
                  />
                </div>
                <div class="list-group">
                  <label>⛔ 群黑名单</label>
                  <el-input
                    :model-value="getListInputValue(listInputKey('plugin', pluginId, 'top', 'blacklist', 'groups'), plugin.top.blacklist?.groups)"
                    @update:model-value="v => updateBlacklist(listInputKey('plugin', pluginId, 'top', 'blacklist', 'groups'), plugin.top, 'groups', v)"
                    placeholder="逗号分隔"
                  />
                </div>
              </div>
            </div>

            <!-- 命令配置 -->
            <div v-if="Object.keys(plugin.commands).length" class="commands-section">
              <div class="section-title">🎯 命令权限配置 ({{ Object.keys(plugin.commands).length }}个命令)</div>
              <div class="commands-grid">
                <div
                  v-for="(cmd, cmdId) in plugin.commands"
                  :key="cmdId"
                  class="command-item"
                >
                  <div class="command-header">
                    <div>
                      <span class="command-name">📌 {{ getCommandName(pluginId, cmdId) }}</span>
                      <span class="command-id">({{ cmdId }})</span>
                    </div>
                    <div class="layer-config compact">
                      <div class="config-row">
                        <label>启用</label>
                        <el-switch v-model="cmd.enabled" size="small" @change="markChanged" />
                      </div>
                      <div class="config-row">
                        <label>💬 场景</label>
                        <el-popover placement="bottom" :width="180" trigger="click">
                          <template #reference>
                            <el-button size="small" class="select-display-btn">
                              {{ getSceneLabel(cmd.scene) }}
                            </el-button>
                          </template>
                          <div class="popover-options">
                            <div
                              v-for="o in sceneOptions"
                              :key="o.value"
                              class="option-item"
                              :class="{ active: cmd.scene === o.value }"
                              @click="cmd.scene = o.value; markChanged()"
                            >
                              {{ o.label }}
                            </div>
                          </div>
                        </el-popover>
                      </div>
                      <div class="config-row">
                        <label>👤 等级</label>
                        <el-popover placement="bottom" :width="180" trigger="click">
                          <template #reference>
                            <el-button size="small" class="select-display-btn">
                              {{ getLevelLabel(cmd.level) }}
                            </el-button>
                          </template>
                          <div class="popover-options">
                            <div
                              v-for="o in levelOptions"
                              :key="o.value"
                              class="option-item"
                              :class="{ active: cmd.level === o.value }"
                              @click="cmd.level = o.value; markChanged()"
                            >
                              {{ o.label }}
                            </div>
                          </div>
                        </el-popover>
                      </div>
                    </div>
                  </div>
                  <div class="list-config compact">
                    <div class="list-group">
                      <label>✅ 用户白名单</label>
                      <el-input
                        :model-value="getListInputValue(listInputKey('plugin', pluginId, 'command', cmdId, 'whitelist', 'users'), cmd.whitelist?.users)"
                        @update:model-value="v => updateWhitelist(listInputKey('plugin', pluginId, 'command', cmdId, 'whitelist', 'users'), cmd, 'users', v)"
                        size="small"
                        placeholder="逗号分隔"
                      />
                    </div>
                    <div class="list-group">
                      <label>✅ 群白名单</label>
                      <el-input
                        :model-value="getListInputValue(listInputKey('plugin', pluginId, 'command', cmdId, 'whitelist', 'groups'), cmd.whitelist?.groups)"
                        @update:model-value="v => updateWhitelist(listInputKey('plugin', pluginId, 'command', cmdId, 'whitelist', 'groups'), cmd, 'groups', v)"
                        size="small"
                        placeholder="逗号分隔"
                      />
                    </div>
                    <div class="list-group">
                      <label>⛔ 用户黑名单</label>
                      <el-input
                        :model-value="getListInputValue(listInputKey('plugin', pluginId, 'command', cmdId, 'blacklist', 'users'), cmd.blacklist?.users)"
                        @update:model-value="v => updateBlacklist(listInputKey('plugin', pluginId, 'command', cmdId, 'blacklist', 'users'), cmd, 'users', v)"
                        size="small"
                        placeholder="逗号分隔"
                      />
                    </div>
                    <div class="list-group">
                      <label>⛔ 群黑名单</label>
                      <el-input
                        :model-value="getListInputValue(listInputKey('plugin', pluginId, 'command', cmdId, 'blacklist', 'groups'), cmd.blacklist?.groups)"
                        @update:model-value="v => updateBlacklist(listInputKey('plugin', pluginId, 'command', cmdId, 'blacklist', 'groups'), cmd, 'groups', v)"
                        size="small"
                        placeholder="逗号分隔"
                      />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </el-collapse-transition>
      </el-card>
    </div>
    </div>

    <!-- JSON编辑对话框 -->
    <el-dialog v-model="jsonDialogVisible" title="编辑权限JSON" width="800">
      <el-input
        v-model="jsonContent"
        type="textarea"
        :rows="20"
        placeholder="在此处编辑权限JSON配置..."
        style="font-family: 'Courier New', monospace;"
      />
      <template #footer>
        <el-button @click="jsonDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveJson">保存JSON</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.permissions-view {
  padding: 24px;
  overflow-y: auto;
  overflow-x: hidden;
  -webkit-overflow-scrolling: touch;

  * {
    box-sizing: border-box;
  }
}

.panel {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 24px;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.2s ease;

  &:hover {
    box-shadow: var(--shadow-md);
  }
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.panel-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--color-text);
  margin: 0;
}

.toolbar-right {
  display: flex;
  gap: 10px;
}

.btn {
  padding: 8px 16px;
  border: none;
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: var(--transition);

  &:hover:not(:disabled) {
    transform: translateY(-2px);
    box-shadow: var(--shadow-md);
  }

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}

.btn-primary {
  background: linear-gradient(135deg, var(--color-primary), var(--color-primary-light));
  color: white;
}

.btn-secondary {
  background: linear-gradient(135deg, var(--color-info), #0891b2);
  color: white;
}

.btn-sm {
  padding: 7px 14px;
  font-size: 13px;
}

.empty-state {
  padding: 60px 20px;
  text-align: center;
  color: var(--color-text-tertiary);
  font-size: 14px;
}

/* ==================== 手风琴样式 ==================== */
.perm-accordion {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.perm-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  transition: box-shadow 0.2s ease;
  will-change: box-shadow;
  contain: layout style paint;

  &:hover {
    box-shadow: var(--shadow-md);
  }

  :deep(.el-card__body) {
    padding: 0;
  }

  &.global-card {
    margin-bottom: 16px;

    :deep(.el-card__header) {
      background: linear-gradient(135deg, rgba(102, 126, 234, 0.1), rgba(118, 75, 162, 0.05));
      cursor: default;
      border-bottom: 2px solid var(--color-border);
      padding: 18px 24px;
    }

    :deep(.el-card__body) {
      padding: 24px;
    }

    .plugin-header {
      display: flex;
      align-items: center;
      justify-content: space-between;

      span:first-child {
        font-weight: 700;
        font-size: 16px;
      }
    }
  }

  :deep(.el-card__header) {
    padding: 18px 24px;
    background: var(--color-bg-tertiary);
    cursor: pointer;
    transition: background-color 0.2s ease;
    user-select: none;
    border-bottom: 1px solid var(--color-border);

    &:hover {
      background: var(--color-border-light);
    }
  }

  .plugin-header {
    display: flex;
    align-items: center;
    gap: 12px;

    &.active {
      :deep(.el-card__header) {
        background: linear-gradient(135deg, rgba(102, 126, 234, 0.1), rgba(118, 75, 162, 0.1));
        border-bottom: 1px solid var(--color-border);
      }
    }
  }

  .expand-icon {
    font-size: 20px;
    transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  }

  .plugin-header.active .expand-icon {
    transform: rotate(90deg);
  }

  .plugin-name {
    font-weight: 700;
    font-size: 16px;
    color: var(--color-text);
  }

  .plugin-id {
    font-size: 13px;
    color: var(--color-text-secondary);
  }

  .enable-switch {
    margin-left: auto;
  }
}

.plugin-body {
  padding: 24px;
  transform: translateZ(0);
  will-change: transform, opacity;
}

/* ==================== 配置区域 ==================== */
.layer-section, .commands-section {
  margin-bottom: 20px;

  &:last-child {
    margin-bottom: 0;
  }
}

.section-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--color-text);
  margin-bottom: 16px;
  padding-bottom: 8px;
  border-bottom: 2px solid var(--color-border);
}

/* ==================== 插件顶级配置 - 行内紧凑样式 ==================== */
.layer-config {
  display: flex;
  flex-wrap: wrap;
  gap: 15px;
  margin-bottom: 20px;
  padding: 15px 20px;
  background: var(--color-bg-tertiary);
  border-radius: var(--radius-md);
  align-items: center;

  &.compact {
    gap: 12px;
    padding: 12px 16px;
    margin-bottom: 10px;
  }

  .config-row {
    display: flex;
    align-items: center;
    gap: 8px;

    label {
      font-size: 14px;
      font-weight: 600;
      color: var(--color-text);
      white-space: nowrap;
    }
  }
}

.select-display-btn {
  min-width: 100px;
  padding: 6px 10px;
  height: auto;
  text-align: center;
  justify-content: center;
  border: 1px solid var(--color-border);
  background: var(--color-bg);
  transition: var(--transition);

  &:hover {
    border-color: var(--color-primary);
  }

  &.el-button--small {
    min-width: 90px;
    padding: 5px 8px;
  }
}

.popover-options {
  display: flex;
  flex-direction: column;
  gap: 4px;

  .option-item {
    padding: 8px 12px;
    cursor: pointer;
    border-radius: var(--radius-sm);
    transition: var(--transition-fast);
    font-size: 14px;

    &:hover {
      background: var(--color-bg-tertiary);
    }

    &.active {
      background: linear-gradient(135deg, rgba(102, 126, 234, 0.15), rgba(118, 75, 162, 0.1));
      color: var(--color-primary);
      font-weight: 700;
    }
  }
}

/* ==================== 白名单/黑名单区域 ==================== */
.list-config {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 16px;
  margin-bottom: 20px;

  @media (max-width: 768px) {
    grid-template-columns: 1fr;
  }

  &.compact {
    gap: 15px;
    margin-bottom: 15px;
  }

  .list-group {
    display: flex;
    flex-direction: column;
    gap: 8px;

    label {
      font-size: 13px;
      font-weight: 600;
      color: var(--color-text-secondary);
    }
  }
}

/* ==================== 命令列表 ==================== */
.commands-section {
  margin-top: 20px;
}

.commands-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;

  @media (max-width: 1200px) {
    grid-template-columns: 1fr;
  }
}

.command-item {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 16px;
  background: var(--color-bg-secondary);
  transition: background-color 0.2s ease, box-shadow 0.2s ease;

  &:hover {
    background: var(--color-bg-tertiary);
    box-shadow: var(--shadow-sm);
  }
}

.command-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 15px;
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--color-border-light);

  .command-name {
    font-weight: 700;
    font-size: 15px;
    color: var(--color-primary);
  }

  .command-id {
    font-size: 12px;
    color: var(--color-text-secondary);
    margin-left: 4px;
  }
}

/* ==================== 命令的行内配置 - 紧凑样式 ==================== */
.command-item .layer-config.compact {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  padding: 0;
  margin-bottom: 0;
  background: transparent;

  .config-row {
    gap: 6px;

    label {
      font-size: 13px;
    }
  }
}

/* ==================== 命令的白名单黑名单 - 2行2列布局 ==================== */
.command-item .list-config.compact {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
  margin-top: 15px;
  margin-bottom: 0;

  .list-group {
    gap: 8px;

    label {
      font-size: 13px;
    }
  }
}
</style>
