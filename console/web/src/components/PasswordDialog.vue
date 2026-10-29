<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { changePassword } from '@/api/auth'

const props = defineProps<{ modelValue: boolean; forced?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const loading = ref(false)
const error = ref('')

watch(visible, (value) => {
  if (!value) return
  oldPassword.value = ''
  newPassword.value = ''
  confirmPassword.value = ''
  error.value = ''
})

const submit = async () => {
  if (!oldPassword.value) {
    error.value = '请输入原密码'
    return
  }
  if (newPassword.value.length < 8) {
    error.value = '新密码至少 8 位'
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    error.value = '两次输入的新密码不一致'
    return
  }
  loading.value = true
  error.value = ''
  try {
    await changePassword(oldPassword.value, newPassword.value)
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '修改密码失败'
  } finally {
    loading.value = false
  }
  if (!error.value) {
    ElMessage.success('密码已修改')
    visible.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="forced ? '首次登录，请修改初始密码' : '修改密码'"
    width="420px"
    append-to-body
    destroy-on-close
  >
    <p v-if="forced" class="pwd-tip">当前还是初始密码，建议立即改成只有你知道的密码。</p>
    <el-form label-position="top" @submit.prevent>
      <el-form-item label="原密码">
        <el-input v-model="oldPassword" type="password" show-password autocomplete="current-password" />
      </el-form-item>
      <el-form-item label="新密码">
        <el-input v-model="newPassword" type="password" show-password autocomplete="new-password" />
      </el-form-item>
      <el-form-item label="确认新密码">
        <el-input
          v-model="confirmPassword"
          type="password"
          show-password
          autocomplete="new-password"
          @keyup.enter="submit"
        />
      </el-form-item>
    </el-form>
    <div v-if="error" class="pwd-error">{{ error }}</div>
    <template #footer>
      <el-button @click="visible = false">{{ forced ? '稍后再说' : '取消' }}</el-button>
      <el-button type="primary" :loading="loading" @click="submit">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped lang="scss">
.pwd-tip {
  margin: 0 0 12px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.pwd-error {
  color: var(--color-danger);
  font-size: 12px;
}
</style>
