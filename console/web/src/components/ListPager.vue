<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
defineProps<{
  page: number
  total: number
  pageSize: number
}>()

const emit = defineEmits<{
  (event: 'update:page', value: number): void
}>()
</script>

<template>
  <el-pagination
    v-if="total > pageSize"
    class="list-pager"
    :current-page="page"
    :page-size="pageSize"
    :total="total"
    layout="prev, pager, next, total"
    small
    @current-change="emit('update:page', $event)"
  />
</template>

<style scoped lang="scss">
.list-pager {
  display: flex;
  justify-content: flex-end;
  padding: 12px 4px 0;
}
</style>
