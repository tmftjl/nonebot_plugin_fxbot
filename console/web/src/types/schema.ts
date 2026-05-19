// -*- coding: utf-8 -*-
/**
 * 配置界面Schema类型定义
 * 参考 guoba-plugin 的组件类型
 */

// 基础输入组件
export type BasicInputComponent =
  | 'Input'           // 单行文本输入
  | 'InputNumber'     // 数字输入
  | 'InputPassword'   // 密码输入
  | 'Textarea'        // 多行文本输入

// 选择组件
export type SelectComponent =
  | 'Switch'          // 开关
  | 'Select'          // 下拉选择
  | 'RadioGroup'      // 单选组
  | 'Checkbox'        // 复选框
  | 'CheckboxGroup'   // 复选框组

// 特殊组件
export type SpecialComponent =
  | 'GTags'           // 标签输入（数组）
  | 'GArrayInput'     // 数组输入（列表编辑）
  | 'GSubForm'        // 子表单（动态对象/数组）
  | 'GSelectGroup'    // 群组选择器
  | 'GSelectFriend'   // 好友选择器
  | 'EasyCron'        // Cron表达式编辑器
  | 'ColorPicker'     // 颜色选择器
  | 'DatePicker'      // 日期选择器
  | 'TimePicker'      // 时间选择器
  | 'Rate'            // 评分
  | 'Slider'          // 滑块

// 布局组件
export type LayoutComponent =
  | 'Divider'         // 分割线（分组标题）

// 所有组件类型
export type ComponentType =
  | BasicInputComponent
  | SelectComponent
  | SpecialComponent
  | LayoutComponent

// Schema项定义
export interface SchemaItem {
  field?: string                      // 字段路径，支持点号嵌套如 "session.timeout"
  label: string                       // 标签文本
  helpMessage?: string                // 行内帮助信息
  bottomHelpMessage?: string          // 底部帮助信息
  component: ComponentType            // 组件类型
  componentProps?: Record<string, any> // 组件属性
  required?: boolean                  // 是否必填
  rules?: Array<{                     // 验证规则
    pattern?: string
    message?: string
    required?: boolean
    min?: number
    max?: number
  }>
}

// 配置卡片定义
export interface ConfigCard {
  key: string                         // 卡片唯一标识
  title: string                       // 卡片标题
  desc?: string                       // 卡片描述
  schemas: SchemaItem[]               // 表单项列表
}

// 配置Tab定义
export interface ConfigTab {
  key: string                         // Tab唯一标识
  title: string                       // Tab标题
  cards: ConfigCard[]                 // 卡片列表
}

// 配置数据类型
export type ConfigData = Record<string, any>
