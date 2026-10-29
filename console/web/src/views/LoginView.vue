<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { Lock, User } from '@element-plus/icons-vue'
import { authState, login } from '@/api/auth'

const username = ref('root')
const password = ref('')
const loading = ref(false)
const error = ref('')

const submit = async () => {
  const name = username.value.trim()
  if (!name || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  error.value = ''
  try {
    await login(name, password.value)
    password.value = ''
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '登录失败'
  } finally {
    loading.value = false
  }
}

const message = computed(() => error.value || authState.message)
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-logo">
        <div class="logo-mark">Fx</div>
        <div class="logo-text">
          <span class="logo-title">FxBot</span>
          <span class="logo-sub">控制台</span>
        </div>
      </div>
      <el-form label-position="top" class="login-form" @submit.prevent>
        <el-form-item label="用户名">
          <el-input
            v-model="username"
            size="large"
            placeholder="用户名"
            :prefix-icon="User"
            autocomplete="username"
          />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="password"
            type="password"
            size="large"
            show-password
            placeholder="密码"
            :prefix-icon="Lock"
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </el-form-item>
      </el-form>
      <div v-if="message" class="login-error">{{ message }}</div>
      <el-button class="login-submit" type="primary" size="large" :loading="loading" @click="submit">
        登录
      </el-button>
      <p class="login-help">初始账户 root，初始密码见机器人启动日志；首次登录后会提醒修改密码。</p>
    </div>
  </div>
</template>

<style scoped lang="scss">
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100vw;
  height: 100vh;
  background:
    radial-gradient(50rem 28rem at 12% 0%, rgba(224, 111, 6, 0.09), transparent 62%),
    radial-gradient(44rem 24rem at 92% 100%, rgba(96, 137, 144, 0.12), transparent 65%),
    var(--color-bg);
}

.login-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 340px;
  padding: 30px 28px 24px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  background: var(--color-bg-card);
  box-shadow: var(--shadow-lg);
}

.login-logo {
  display: flex;
  align-items: center;
  gap: 10px;
}

.logo-mark {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: var(--color-primary);
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.logo-text {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}

.logo-title {
  color: var(--color-text);
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.01em;
}

.logo-sub {
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.login-form {
  margin-top: 4px;

  :deep(.el-form-item) {
    margin-bottom: 12px;
  }

  :deep(.el-form-item__label) {
    padding-bottom: 4px;
    color: var(--color-text-secondary);
    font-size: 12px;
    line-height: 1.4;
  }
}

.login-error {
  color: var(--color-danger);
  font-size: 12px;
}

.login-submit {
  width: 100%;
}

.login-help {
  margin: 2px 0 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.6;
}
</style>
