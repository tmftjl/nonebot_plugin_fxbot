// -*- coding: utf-8 -*-
import { computed, ref, watch } from 'vue'

type ListSource<T> = { readonly value: T[] }

/** 前端分页：数据源变化时回到第一页，总数缩小时收敛到最后一页。 */
export function usePagination<T>(source: ListSource<T>, pageSize = 20) {
  const page = ref(1)
  const total = computed(() => source.value.length)
  const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
  const paged = computed(() => {
    const start = (page.value - 1) * pageSize
    return source.value.slice(start, start + pageSize)
  })

  watch(
    () => source.value,
    () => {
      page.value = 1
    },
  )
  watch(pageCount, (count) => {
    if (page.value > count) page.value = count
  })

  return { page, total, pageCount, paged }
}
