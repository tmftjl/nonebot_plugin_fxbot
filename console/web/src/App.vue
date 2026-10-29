<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch, type Component } from 'vue'
import {
  ArrowDown,
  ArrowRight,
  Avatar,
  ChatDotRound,
  ChatLineSquare,
  Coin,
  Collection,
  Connection,
  CreditCard,
  DataAnalysis,
  Expand,
  Fold,
  Lock,
  Moon,
  Odometer,
  Setting,
  Sunny,
  Tickets,
  User,
} from '@element-plus/icons-vue'
import DashboardView from '@/views/DashboardView.vue'
import ConfigView from '@/views/ConfigView.vue'
import PermissionsView from '@/views/PermissionsView.vue'
import MembershipView from '@/views/MembershipView.vue'
import PersonasView from '@/views/PersonasView.vue'
import ProviderView from '@/views/ProviderView.vue'
import StatsView from '@/views/StatsView.vue'
import LogsView from '@/views/LogsView.vue'
import CacheView from '@/views/CacheView.vue'
import AmemorixView from '@/views/AmemorixView.vue'
import ChatMemoryView from '@/views/ChatMemoryView.vue'
import ChatReplyView from '@/views/ChatReplyView.vue'
import ChatSettingsView from '@/views/ChatSettingsView.vue'
import LoginView from '@/views/LoginView.vue'
import PasswordDialog from '@/components/PasswordDialog.vue'
import { configApi } from '@/api'
import { authState, clearToken, loadProfile } from '@/api/auth'
import type { ConfigTab } from '@/types/schema'

interface NavItem {
  index: string
  label: string
  icon?: Component
}

interface NavSection {
  key: string
  title: string
  items: NavItem[]
}

const ACTIVE_KEY = 'fxbot-console-active'
const COLLAPSED_KEY = 'fxbot-console-sidebar-collapsed'
const CONFIG_OPEN_KEY = 'fxbot-console-config-open'

const activeMenu = ref(localStorage.getItem(ACTIVE_KEY) || 'dashboard')
const isDark = ref(localStorage.getItem('theme') === 'dark')
const sidebarCollapsed = ref(localStorage.getItem(COLLAPSED_KEY) === 'true')
const configExpanded = ref(
  localStorage.getItem(CONFIG_OPEN_KEY) === null
    ? activeMenu.value.startsWith('config')
    : localStorage.getItem(CONFIG_OPEN_KEY) === 'true',
)
const mobileOpen = ref(false)
const isMobile = ref(false)
const configTabs = ref<ConfigTab[]>([])

const activeConfigTab = computed(() =>
  activeMenu.value.startsWith('config-') ? activeMenu.value.replace('config-', '') : '',
)

const fixedSections: NavSection[] = [
  {
    key: 'overview',
    title: '概览',
    items: [{ index: 'dashboard', label: '仪表盘', icon: Odometer }],
  },
  {
    key: 'ops',
    title: '运营',
    items: [
      { index: 'membership', label: '会员续费', icon: CreditCard },
      { index: 'stats', label: '消息统计', icon: DataAnalysis },
      { index: 'logs', label: '运行日志', icon: Tickets },
    ],
  },
  {
    key: 'ai',
    title: 'AI 对话',
    items: [
      { index: 'chat-memory', label: '学习', icon: ChatDotRound },
      { index: 'amemorix', label: '长期记忆', icon: Collection },
      { index: 'chat-reply', label: '接话与运行', icon: ChatLineSquare },
      { index: 'chat-settings', label: '对话设置', icon: Setting },
      { index: 'personas', label: '人格管理', icon: Avatar },
      { index: 'providers', label: '模型配置', icon: Connection },
    ],
  },
  {
    key: 'system',
    title: '系统',
    items: [
      { index: 'permissions', label: '权限管理', icon: Lock },
      { index: 'cache', label: '缓存', icon: Coin },
    ],
  },
]

const sections = computed<NavSection[]>(() => [
  ...fixedSections,
  {
    key: 'config',
    title: '插件配置',
    items: configTabs.value.length
      ? configTabs.value.map((tab) => ({ index: `config-${tab.key}`, label: tab.title }))
      : [{ index: 'config', label: '插件配置', icon: Setting }],
  },
])

const allNavItems = computed(() =>
  sections.value.flatMap((section) =>
    section.items.map((item) => ({ ...item, sectionKey: section.key, sectionTitle: section.title })),
  ),
)

const activeNav = computed(() => allNavItems.value.find((item) => item.index === activeMenu.value))
const activeLabel = computed(() => activeNav.value?.label || '控制台')
const activeSectionTitle = computed(() => activeNav.value?.sectionTitle || '')
const activeConfigItem = computed(() =>
  sections.value.find((section) => section.key === 'config')?.items.find((item) => item.index === activeMenu.value),
)
const sidebarIsCollapsed = computed(() => sidebarCollapsed.value && !isMobile.value)

const passwordVisible = ref(false)
const forcedChange = ref(false)

const logout = () => {
  clearToken()
  mobileOpen.value = false
}

const openPassword = (forced = false) => {
  forcedChange.value = forced
  passwordVisible.value = true
}

const onUserCommand = (command: string) => {
  if (command === 'password') openPassword()
  if (command === 'logout') logout()
}

const toggleTheme = () => {
  isDark.value = !isDark.value
  localStorage.setItem('theme', isDark.value ? 'dark' : 'light')
  document.documentElement.classList.toggle('dark', isDark.value)
}

const updateViewport = () => {
  isMobile.value = window.matchMedia('(max-width: 900px)').matches
  if (!isMobile.value) mobileOpen.value = false
}

const toggleSidebar = () => {
  if (isMobile.value) {
    mobileOpen.value = !mobileOpen.value
    return
  }
  sidebarCollapsed.value = !sidebarCollapsed.value
}

const select = (index: string) => {
  activeMenu.value = index
  mobileOpen.value = false
}

const toggleConfigGroup = () => {
  configExpanded.value = !configExpanded.value
}

const focusConfig = () => {
  if (sidebarCollapsed.value) sidebarCollapsed.value = false
  configExpanded.value = true
  const first = configTabs.value[0]
  select(first ? `config-${first.key}` : 'config')
}

const loadConfigTabs = async () => {
  try {
    const tabs = await configApi.getTabs()
    configTabs.value = [...(tabs || [])].sort((a, b) => {
      const orderA = Number.isFinite(a.order) ? a.order! : 1000
      const orderB = Number.isFinite(b.order) ? b.order! : 1000
      return orderA - orderB || a.key.localeCompare(b.key)
    })
    if (activeMenu.value.startsWith('config') && configTabs.value.length) {
      const known = configTabs.value.some((tab) => `config-${tab.key}` === activeMenu.value)
      if (!known) activeMenu.value = `config-${configTabs.value[0].key}`
    }
  } catch (e) {
    console.error('加载配置tabs失败', e)
  }
}

watch(activeMenu, (value) => {
  localStorage.setItem(ACTIVE_KEY, value)
})

watch(sidebarCollapsed, (value) => {
  localStorage.setItem(COLLAPSED_KEY, String(value))
})

watch(configExpanded, (value) => {
  localStorage.setItem(CONFIG_OPEN_KEY, String(value))
})

if (isDark.value) {
  document.documentElement.classList.add('dark')
}

watch(
  () => authState.token,
  (token) => {
    if (token) loadConfigTabs()
  },
)

watch(
  () => authState.mustChange,
  (value) => {
    // 首次登录仍在用初始密码：进控制台就提醒改密，关掉后本次会话不再弹
    if (value) openPassword(true)
  },
)

onMounted(async () => {
  updateViewport()
  window.addEventListener('resize', updateViewport)
  if (!authState.token) return
  loadConfigTabs()
  await loadProfile()
})

onUnmounted(() => {
  window.removeEventListener('resize', updateViewport)
})
</script>

<template>
  <LoginView v-if="!authState.token" />
  <div v-else class="app-shell">
    <div v-if="mobileOpen" class="sidebar-backdrop" @click="mobileOpen = false" />

    <aside
      class="app-sidebar"
      :class="{ collapsed: sidebarIsCollapsed, 'mobile-open': mobileOpen }"
    >
      <div class="sidebar-logo">
        <div class="logo-mark">Fx</div>
        <div class="logo-text">
          <span class="logo-title">FxBot</span>
          <span class="logo-sub">控制台</span>
        </div>
      </div>

      <nav class="sidebar-nav">
        <div v-for="section in sections" :key="section.key" class="nav-section">
          <template v-if="section.key === 'config'">
            <button
              v-if="!sidebarIsCollapsed"
              class="nav-section-title nav-section-toggle"
              type="button"
              @click="toggleConfigGroup"
            >
              <span>{{ section.title }}</span>
              <el-icon class="section-chevron">
                <ArrowDown v-if="configExpanded" />
                <ArrowRight v-else />
              </el-icon>
            </button>
          </template>
          <div v-else class="nav-section-title">{{ section.title }}</div>

          <template v-if="section.key === 'config'">
            <button
              v-if="sidebarIsCollapsed"
              class="nav-item"
              :class="{ active: activeMenu.startsWith('config') }"
              type="button"
              title="插件配置"
              @click="focusConfig"
            >
              <el-icon class="nav-icon"><Setting /></el-icon>
              <span class="nav-label">插件配置</span>
            </button>
            <template v-else-if="configExpanded || !configTabs.length">
              <button
                v-for="item in section.items"
                :key="item.index"
                class="nav-item nav-item-sub"
                :class="{ active: activeMenu === item.index }"
                type="button"
                :title="item.label"
                @click="select(item.index)"
              >
                <span class="nav-bullet" />
                <span class="nav-label">{{ item.label }}</span>
              </button>
            </template>
            <button
              v-else-if="activeConfigItem"
              class="nav-item nav-item-sub active"
              type="button"
              :title="activeConfigItem.label"
              @click="select(activeConfigItem.index)"
            >
              <span class="nav-bullet" />
              <span class="nav-label">{{ activeConfigItem.label }}</span>
            </button>
          </template>

          <template v-else>
            <button
              v-for="item in section.items"
              :key="item.index"
              class="nav-item"
              :class="{ active: activeMenu === item.index }"
              type="button"
              :title="item.label"
              @click="select(item.index)"
            >
              <el-icon class="nav-icon"><component :is="item.icon" /></el-icon>
              <span class="nav-label">{{ item.label }}</span>
            </button>
          </template>
        </div>
      </nav>
    </aside>

    <div class="app-body">
      <header class="app-header">
        <div class="header-left">
          <button
            class="icon-btn"
            type="button"
            :title="isMobile ? '打开菜单' : sidebarCollapsed ? '展开侧栏' : '收起侧栏'"
            @click="toggleSidebar"
          >
            <el-icon :size="18">
              <Expand v-if="sidebarCollapsed || isMobile" />
              <Fold v-else />
            </el-icon>
          </button>
          <div class="breadcrumb">
            <span class="crumb-section">{{ activeSectionTitle }}</span>
            <span class="crumb-sep">/</span>
            <span class="crumb-page">{{ activeLabel }}</span>
          </div>
        </div>
        <div class="header-right">
          <span class="header-status">
            <span class="status-dot" />
            在线
          </span>
          <button
            class="icon-btn"
            type="button"
            :title="isDark ? '切换为浅色模式' : '切换为深色模式'"
            @click="toggleTheme"
          >
            <el-icon :size="18">
              <Sunny v-if="isDark" />
              <Moon v-else />
            </el-icon>
          </button>
          <el-dropdown trigger="click" @command="onUserCommand">
            <button class="user-chip" type="button" title="账户">
              <el-icon :size="15"><User /></el-icon>
              <span class="user-name">{{ authState.username || '账户' }}</span>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="password">修改密码</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <main class="app-main">
        <DashboardView v-if="activeMenu === 'dashboard'" @navigate="activeMenu = $event" />
        <MembershipView v-else-if="activeMenu === 'membership'" />
        <StatsView v-else-if="activeMenu === 'stats'" />
        <LogsView v-else-if="activeMenu === 'logs'" />
        <CacheView v-else-if="activeMenu === 'cache'" />
        <ChatMemoryView v-else-if="activeMenu === 'chat-memory'" />
        <ChatReplyView v-else-if="activeMenu === 'chat-reply'" />
        <AmemorixView v-else-if="activeMenu === 'amemorix'" />
        <ChatSettingsView v-else-if="activeMenu === 'chat-settings'" />
        <PermissionsView v-else-if="activeMenu === 'permissions'" />
        <PersonasView v-else-if="activeMenu === 'personas'" />
        <ProviderView v-else-if="activeMenu === 'providers'" />
        <ConfigView v-else-if="activeMenu.startsWith('config')" :active-tab="activeConfigTab" />
      </main>
    </div>
  </div>

  <PasswordDialog v-if="authState.token" v-model="passwordVisible" :forced="forcedChange" />
</template>

<style scoped lang="scss">
.app-shell {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background: var(--color-bg);
}

.app-sidebar {
  display: flex;
  flex: none;
  flex-direction: column;
  width: var(--layout-sidebar-width);
  border-right: 1px solid var(--color-border);
  background: var(--color-bg-card);
  transition: width 0.2s ease;
  overflow: hidden;

  &.collapsed {
    width: var(--layout-sidebar-collapsed-width);
  }
}

.sidebar-logo {
  display: flex;
  flex: none;
  align-items: center;
  gap: 10px;
  height: var(--layout-header-height);
  padding: 0 16px;
  border-bottom: 1px solid var(--color-border-light);

  .app-sidebar.collapsed & {
    justify-content: center;
    padding: 0;
  }
}

.logo-mark {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: var(--color-primary);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.logo-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.2;

  .app-sidebar.collapsed & {
    display: none;
  }
}

.logo-title {
  color: var(--color-text);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: -0.01em;
}

.logo-sub {
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.sidebar-nav {
  flex: 1;
  min-height: 0;
  padding: 14px 10px 20px;
  overflow-y: auto;
}

.nav-section + .nav-section {
  margin-top: 18px;
}

.nav-section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  height: 24px;
  padding: 0 8px;
  border: 0;
  background: transparent;
  color: var(--color-text-tertiary);
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.02em;
  text-align: left;

  .app-sidebar.collapsed & {
    display: none;
  }
}

.nav-section-toggle {
  cursor: pointer;

  &:hover {
    color: var(--color-text-secondary);
  }
}

.section-chevron {
  font-size: 12px;
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: var(--layout-nav-item-height);
  margin-top: 2px;
  padding: 0 10px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: 14px;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;

  &:hover {
    background: var(--color-bg-tertiary);
    color: var(--color-text);
  }

  &.active {
    background: transparent;
    color: var(--color-primary);
    font-weight: 600;

    &::before {
      content: '';
      position: absolute;
      top: 8px;
      bottom: 8px;
      left: -10px;
      width: 2px;
      border-radius: 0 2px 2px 0;
      background: var(--color-primary);
    }

    &:hover {
      background: var(--color-bg-tertiary);
    }
  }

  .app-sidebar.collapsed & {
    justify-content: center;
    padding: 0;
  }
}

.nav-icon {
  flex: none;
  font-size: 17px;
}

.nav-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;

  .app-sidebar.collapsed & {
    display: none;
  }
}

.nav-item-sub {
  height: 34px;
  padding-left: 12px;
  font-size: 13px;
}

.nav-bullet {
  flex: none;
  width: 4px;
  height: 4px;
  margin: 0 7px;
  border-radius: 50%;
  background: var(--color-text-tertiary);

  .nav-item-sub.active & {
    background: var(--color-primary);
  }
}

.app-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.app-header {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  height: var(--layout-header-height);
  padding: 0 20px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-bg);
}

.header-left,
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.user-chip {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;

  &:hover {
    background: var(--color-bg-tertiary);
    color: var(--color-text);
  }
}

.icon-btn {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;

  &:hover {
    background: var(--color-bg-tertiary);
    color: var(--color-text);
  }
}

.breadcrumb {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-size: 13px;
}

.crumb-section,
.crumb-sep {
  color: var(--color-text-tertiary);
}

.crumb-page {
  overflow: hidden;
  color: var(--color-text);
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-status {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-success);
}

.app-main {
  flex: 1;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
}

.sidebar-backdrop {
  display: none;
}

@media (max-width: 900px) {
  .app-sidebar {
    position: fixed;
    top: 0;
    bottom: 0;
    left: 0;
    z-index: 60;
    width: var(--layout-sidebar-width);
    transform: translateX(-100%);
    box-shadow: var(--shadow-lg);
    transition: transform 0.22s ease;

    &.mobile-open {
      transform: translateX(0);
    }

    &.collapsed {
      width: var(--layout-sidebar-width);
    }

    &.collapsed .sidebar-logo {
      justify-content: flex-start;
      padding: 0 16px;
    }

    &.collapsed .logo-text,
    &.collapsed .nav-label,
    &.collapsed .nav-section-title {
      display: flex;
    }

    &.collapsed .nav-item {
      justify-content: flex-start;
      padding: 0 10px;
    }
  }

  .sidebar-backdrop {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 55;
    background: rgba(2, 8, 23, 0.4);
  }

  .header-status,
  .crumb-section,
  .crumb-sep {
    display: none;
  }
}
</style>
