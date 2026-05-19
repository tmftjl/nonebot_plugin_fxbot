<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { Fold, Expand } from '@element-plus/icons-vue'
import DashboardView from '@/views/DashboardView.vue'
import ConfigView from '@/views/ConfigView.vue'
import PermissionsView from '@/views/PermissionsView.vue'
import MembershipView from '@/views/MembershipView.vue'
import StatsView from '@/views/StatsView.vue'
import { configApi } from '@/api'
import type { ConfigTab } from '@/types/schema'

const activeMenu = ref('dashboard')
const isDark = ref(localStorage.getItem('theme') === 'dark')
const sidebarCollapsed = ref(false)
const configTabs = ref<ConfigTab[]>([])

// 当前激活的配置Tab
const activeConfigTab = ref('')

const toggleTheme = () => {
  isDark.value = !isDark.value
  localStorage.setItem('theme', isDark.value ? 'dark' : 'light')
  document.documentElement.classList.toggle('dark', isDark.value)
}

const toggleSidebar = () => {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

const handleMenuSelect = (index: string) => {
  if (index.startsWith('config-')) {
    activeMenu.value = index
    activeConfigTab.value = index.replace('config-', '')
  } else {
    activeMenu.value = index
    activeConfigTab.value = ''
  }
}

// 加载配置tabs
const loadConfigTabs = async () => {
  try {
    const tabs = await configApi.getTabs()
    configTabs.value = tabs || []
  } catch (e) {
    console.error('加载配置tabs失败', e)
  }
}

if (isDark.value) {
  document.documentElement.classList.add('dark')
}

onMounted(() => {
  loadConfigTabs()
})

const sidebarWidth = computed(() => sidebarCollapsed.value ? '64px' : '200px')
</script>

<template>
  <el-container class="app-container">
    <el-header class="app-header">
      <div class="header-left">
        <el-button text @click="toggleSidebar" class="collapse-btn">
          <el-icon :size="20">
            <Fold v-if="!sidebarCollapsed" />
            <Expand v-else />
          </el-icon>
        </el-button>
        <h1 class="logo">FxBot 控制台</h1>
      </div>
      <div class="header-right">
        <el-button circle @click="toggleTheme">
          <el-icon v-if="isDark"><Sunny /></el-icon>
          <el-icon v-else><Moon /></el-icon>
        </el-button>
      </div>
    </el-header>

    <el-container class="app-body">
      <el-aside :width="sidebarWidth" class="app-sidebar">
        <el-menu
          :default-active="activeMenu"
          :collapse="sidebarCollapsed"
          @select="handleMenuSelect"
        >
          <el-menu-item index="dashboard">
            <el-icon><Odometer /></el-icon>
            <template #title>仪表盘</template>
          </el-menu-item>
          <el-menu-item index="membership">
            <el-icon><CreditCard /></el-icon>
            <template #title>会员续费</template>
          </el-menu-item>
          <el-menu-item index="stats">
            <el-icon><DataAnalysis /></el-icon>
            <template #title>消息统计</template>
          </el-menu-item>
          <el-menu-item index="permissions">
            <el-icon><Lock /></el-icon>
            <template #title>权限管理</template>
          </el-menu-item>
          <!-- 插件配置子菜单 -->
          <el-sub-menu index="config" v-if="configTabs.length">
            <template #title>
              <el-icon><Setting /></el-icon>
              <span>插件配置</span>
            </template>
            <el-menu-item
              v-for="tab in configTabs"
              :key="tab.key"
              :index="'config-' + tab.key"
            >
              {{ tab.title }}
            </el-menu-item>
          </el-sub-menu>
          <el-menu-item v-else index="config">
            <el-icon><Setting /></el-icon>
            <template #title>插件配置</template>
          </el-menu-item>
        </el-menu>
      </el-aside>

      <el-main class="app-main">
        <DashboardView v-if="activeMenu === 'dashboard'" />
        <MembershipView v-else-if="activeMenu === 'membership'" />
        <StatsView v-else-if="activeMenu === 'stats'" />
        <PermissionsView v-else-if="activeMenu === 'permissions'" />
        <ConfigView v-else-if="activeMenu.startsWith('config')" :active-tab="activeConfigTab" />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped lang="scss">
.app-container {
  height: 100vh;
  width: 100vw;
  overflow: hidden;

  .app-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0 24px;
    
    .header-left {
      display: flex;
      align-items: center;
      gap: 16px;

      .collapse-btn {
        padding: 8px;
        transition: all 0.3s ease;
        border-radius: 8px;

        &:hover {
          background-color: var(--color-bg-tertiary);
          color: var(--color-primary);
        }
      }

      .logo {
        margin: 0;
        font-size: 22px;
        letter-spacing: -0.02em;
        font-weight: 800;
        background: linear-gradient(135deg, var(--color-primary), var(--color-info));
        -webkit-background-clip: text;
        background-clip: text;
        -webkit-text-fill-color: transparent;
        text-shadow: 0 2px 10px rgba(59, 130, 246, 0.2);
        transition: all 0.3s ease;
        cursor: default;

        &:hover {
          filter: brightness(1.1);
        }
      }
    }

    .header-right {
      :deep(.el-button) {
        transition: all 0.3s;
        border: none;
        background: transparent;

        &:hover {
          transform: rotate(15deg) scale(1.1);
          color: var(--color-warning);
          background: var(--color-bg-tertiary);
        }
      }
    }
  }

  .app-body {
    height: calc(100vh - 60px);
    overflow: hidden;

    .app-sidebar {
      transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
      overflow-x: hidden;
      overflow-y: auto;

      &::-webkit-scrollbar {
        display: none;
      }

      scrollbar-width: none;
      -ms-overflow-style: none;

      :deep(.el-menu) {
        border-right: none;
        padding: 16px 8px;

        &.el-menu--collapse {
          width: 64px;
          padding: 16px 4px;
        }

        .el-menu-item, .el-sub-menu__title {
          margin-bottom: 4px;
          border-radius: 12px;
          height: 48px;
          line-height: 48px;
          
          &:hover {
            background-color: var(--color-bg-tertiary);
            transform: translateX(4px);
            color: var(--color-primary);
          }

          &.is-active {
            box-shadow: var(--shadow-glow);
            font-weight: 600;
            transform: translateX(4px);
          }
           
          .el-icon {
             font-size: 18px;
             margin-right: 12px;
          }
        }
      }

      /* 子菜单样式优化：层级感 */
      :deep(.el-sub-menu) {
        .el-menu {
            padding: 4px 0 4px 20px; /* 增加左侧缩进 */
            margin-top: 0;
            background-color: transparent;
        }

        .el-menu-item {
          font-size: 13px;
          height: 36px;
          line-height: 36px;
          margin-left: 10px;
          margin-bottom: 2px;
          min-width: 160px;
          border-radius: 8px;
          color: var(--color-text-secondary);
          
          &:hover {
              background: var(--color-bg-tertiary);
              color: var(--color-text);
          }
          
          &.is-active {
              background: var(--color-primary-light);
              color: var(--color-primary);
              box-shadow: none; /* 子菜单激活不发光 */
          }
        }
      }

      :deep(.el-sub-menu__title) {
        padding-right: 30px;
      }
    }

    .app-main {
      padding: 0;
      overflow-y: auto;
      overflow-x: hidden;
      height: 100%;
      background-color: var(--color-bg);

      &::-webkit-scrollbar {
        display: none;
      }

      scrollbar-width: none;
      -ms-overflow-style: none;
    }
  }
}
</style>
