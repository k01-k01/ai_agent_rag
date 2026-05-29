import { useState, useEffect, useCallback, useRef } from 'react';
import {
  listKnowledgeBases,
  createKnowledgeBase,
  deleteKnowledgeBase,
  updateKnowledgeBase,
  listDocuments,
  uploadDocument,
  deleteDocument,
  KnowledgeBase as KB,
  DocumentItem,
} from '../api/index';

function KnowledgeBase() {
  const [knowledgeBases, setKnowledgeBases] = useState<KB[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newName, setNewName] = useState('');
  const [deleting, setDeleting] = useState<string | null>(null);

  // 编辑知识库相关状态
  const [editingKb, setEditingKb] = useState<KB | null>(null);
  const [editName, setEditName] = useState('');

  // 文档管理相关状态
  const [expandedKbId, setExpandedKbId] = useState<string | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [deletingDocId, setDeletingDocId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 摘要弹窗相关状态
  const [summaryDoc, setSummaryDoc] = useState<DocumentItem | null>(null);

  const fetchList = useCallback(async () => {
    try {
      const data = await listKnowledgeBases();
      setKnowledgeBases(data);
    } catch (err) {
      console.error('Failed to load knowledge bases:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchList();
  }, [fetchList]);

  // 停止轮询
  const stopPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  // 检查是否所有文档都已处理完成
  const isAllDocsProcessed = useCallback((docs: DocumentItem[]) => {
    return docs.every(doc => doc.status === 'completed' || doc.status === 'error');
  }, []);

  // 加载文档列表
  const loadDocuments = async (kbId: string) => {
    setDocsLoading(true);
    try {
      const docs = await listDocuments(kbId);
      setDocuments(docs);
      // 如果还有文档在处理中，启动轮询
      if (!isAllDocsProcessed(docs)) {
        startPolling(kbId);
      } else {
        stopPolling();
      }
    } catch (err) {
      console.error('Failed to load documents:', err);
      setDocuments([]);
    } finally {
      setDocsLoading(false);
    }
  };

  // 启动轮询，每 3 秒刷新文档状态
  const startPolling = useCallback((kbId: string) => {
    stopPolling();
    pollingRef.current = setInterval(async () => {
      try {
        const docs = await listDocuments(kbId);
        setDocuments(docs);
        // 所有文档处理完成则停止轮询
        if (isAllDocsProcessed(docs)) {
          stopPolling();
        }
      } catch (err) {
        console.error('Polling failed:', err);
      }
    }, 3000);
  }, [stopPolling, isAllDocsProcessed]);

  // 组件卸载时清理轮询
  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  const toggleExpand = (kbId: string) => {
    if (expandedKbId === kbId) {
      setExpandedKbId(null);
      setDocuments([]);
      stopPolling();
    } else {
      setExpandedKbId(kbId);
      loadDocuments(kbId);
    }
  };


  const handleCreate = async () => {
    const trimmed = newName.trim();
    if (!trimmed) return;
    try {
      await createKnowledgeBase(trimmed);
      setNewName('');
      setShowCreateModal(false);
      await fetchList();
    } catch (err) {
      console.error('Failed to create knowledge base:', err);
      alert('创建知识库失败，请稍后再试。');
    }
  };

  const handleDelete = async (id: string) => {
    if (!window.confirm('确定要删除该知识库吗？所有关联的文档也将被删除。')) return;
    setDeleting(id);
    try {
      await deleteKnowledgeBase(id);
      if (expandedKbId === id) {
        setExpandedKbId(null);
        setDocuments([]);
      }
      await fetchList();
    } catch (err) {
      console.error('Failed to delete knowledge base:', err);
      alert('删除知识库失败，请稍后再试。');
    } finally {
      setDeleting(null);
    }
  };

  // 编辑知识库
  const handleEdit = (kb: KB) => {
    setEditingKb(kb);
    setEditName(kb.name);
  };

  const handleEditSubmit = async () => {
    if (!editingKb || !editName.trim()) return;
    try {
      await updateKnowledgeBase(editingKb.id, editName.trim());
      setEditingKb(null);
      setEditName('');
      await fetchList();
    } catch (err) {
      console.error('Failed to update knowledge base:', err);
      alert('编辑知识库失败，请稍后再试。');
    }
  };

  // 文档上传
  const handleUpload = async (kbId: string, file: File) => {
    setUploading(true);
    try {
      await uploadDocument(kbId, file);
      await loadDocuments(kbId);
    } catch (err: any) {
      console.error('Failed to upload document:', err);
      const msg = err?.data?.error || '文档上传失败，请稍后再试。';
      alert(msg);
    } finally {
      setUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  // 文档删除
  const handleDeleteDocument = async (kbId: string, docId: string) => {
    if (!window.confirm('确定要删除该文档吗？')) return;
    setDeletingDocId(docId);
    try {
      await deleteDocument(kbId, docId);
      await loadDocuments(kbId);
    } catch (err: any) {
      console.error('Failed to delete document:', err);
      const msg = err?.data?.error || '文档删除失败，请稍后再试。';
      alert(msg);
    } finally {
      setDeletingDocId(null);
    }
  };

  const getStatusBadge = (status: string) => {
    const map: Record<string, { text: string; color: string }> = {
      uploaded: { text: '待处理', color: 'bg-yellow-100 text-yellow-800' },
      processing: { text: '处理中', color: 'bg-blue-100 text-blue-800' },
      completed: { text: '已完成', color: 'bg-green-100 text-green-800' },
      error: { text: '失败', color: 'bg-red-100 text-red-800' },
    };

    const info = map[status] || { text: status, color: 'bg-gray-100 text-gray-800' };
    return (
      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${info.color}`}>
        {info.text}
      </span>
    );
  };

  return (
    <div className="max-w-4xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">知识库管理</h1>
        <button
          onClick={() => setShowCreateModal(true)}
          className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition"
        >
          + 创建知识库
        </button>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        className="hidden"
        accept=".txt,.md,.pdf,.doc,.docx"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file && expandedKbId) {
            handleUpload(expandedKbId, file);
          }
        }}
      />

      {loading ? (
        <p className="text-gray-400">加载中...</p>
      ) : knowledgeBases.length === 0 ? (
        <div className="text-center py-12 text-gray-400">
          <p className="text-lg mb-2">暂无知识库</p>
          <p>点击"创建知识库"开始使用</p>
        </div>
      ) : (
        <div className="space-y-3">
          {knowledgeBases.map((kb) => (
            <div key={kb.id}>
              <div
                className="border rounded-lg p-4 hover:shadow transition cursor-pointer"
                onClick={() => toggleExpand(kb.id)}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <span className="text-lg">
                      {expandedKbId === kb.id ? '▼' : '▶'}
                    </span>
                    <div>
                      <h3 className="font-semibold text-lg">{kb.name}</h3>
                      <p className="text-sm text-gray-400">
                        创建于：{new Date(kb.createdAt).toLocaleString('zh-CN')}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleEdit(kb);
                      }}
                      className="px-3 py-1 text-sm text-blue-600 border border-blue-300 rounded hover:bg-blue-50 transition"
                    >
                      编辑
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(kb.id);
                      }}
                      disabled={deleting === kb.id}
                      className="px-3 py-1 text-sm text-red-600 border border-red-300 rounded hover:bg-red-50 transition disabled:opacity-50"
                    >
                      {deleting === kb.id ? '删除中...' : '删除'}
                    </button>
                  </div>
                </div>
              </div>

              {/* 文档列表区域 */}
              {expandedKbId === kb.id && (
                <div className="ml-8 mt-2 border-l-2 border-blue-200 pl-4">
                  <div className="flex items-center justify-between mb-3">
                    <h4 className="text-sm font-semibold text-gray-600">文档列表</h4>
                    <button
                      onClick={() => fileInputRef.current?.click()}
                      disabled={uploading}
                      className="px-3 py-1 text-sm bg-green-600 text-white rounded hover:bg-green-700 transition disabled:opacity-50"
                    >
                      {uploading ? '上传中...' : '+ 上传文档'}
                    </button>
                  </div>

                  {docsLoading ? (
                    <p className="text-sm text-gray-400 py-4">加载文档列表...</p>
                  ) : documents.length === 0 ? (
                    <p className="text-sm text-gray-400 py-4">
                      暂无文档，点击"上传文档"添加（支持 txt, md, pdf, doc, docx）
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {documents.map((doc) => (
                        <div
                          key={doc.id}
                          className="flex items-center justify-between py-2 px-3 bg-gray-50 rounded"
                        >
                          <div className="flex items-center gap-3 min-w-0">
                            <span className="text-sm font-medium truncate">{doc.name}</span>
                            {getStatusBadge(doc.status)}
                            <span className="text-xs text-gray-400 whitespace-nowrap">
                              {(doc.fileSize / 1024).toFixed(1)} KB
                            </span>
                          </div>
                          <div className="flex items-center gap-2 flex-shrink-0">
                            {doc.status === 'completed' && doc.summary && (
                              <button
                                onClick={() => setSummaryDoc(doc)}
                                className="px-2 py-0.5 text-xs text-blue-500 border border-blue-200 rounded hover:bg-blue-50 transition"
                              >
                                提问导读
                              </button>
                            )}
                            <button
                              onClick={() => handleDeleteDocument(kb.id, doc.id)}
                              disabled={deletingDocId === doc.id}
                              className="px-2 py-0.5 text-xs text-red-500 border border-red-200 rounded hover:bg-red-50 transition disabled:opacity-50"
                            >
                              {deletingDocId === doc.id ? '删除中...' : '删除'}
                            </button>
                            <span className="text-xs text-gray-400">
                              {new Date(doc.createdAt).toLocaleString('zh-CN')}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 创建弹窗 */}
      {showCreateModal && (
        <div className="fixed inset-0 flex items-center justify-center z-50 backdrop-blur-sm bg-black/30">
          <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-sm">
            <h2 className="text-lg font-bold mb-4">创建知识库</h2>
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              placeholder="请输入知识库名称"
              className="w-full border rounded px-3 py-2 mb-4 focus:outline-none focus:ring-2 focus:ring-blue-500"
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => {
                  setShowCreateModal(false);
                  setNewName('');
                }}
                className="px-4 py-2 text-gray-600 border rounded hover:bg-gray-50 transition"
              >
                取消
              </button>
              <button
                onClick={handleCreate}
                disabled={!newName.trim()}
                className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition disabled:opacity-50"
              >
                确定
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 编辑弹窗 */}
      {editingKb && (
        <div className="fixed inset-0 flex items-center justify-center z-50 backdrop-blur-sm bg-black/30">
          <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-sm">
            <h2 className="text-lg font-bold mb-4">编辑知识库</h2>
            <input
              type="text"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleEditSubmit()}
              placeholder="请输入知识库名称"
              className="w-full border rounded px-3 py-2 mb-4 focus:outline-none focus:ring-2 focus:ring-blue-500"
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => {
                  setEditingKb(null);
                  setEditName('');
                }}
                className="px-4 py-2 text-gray-600 border rounded hover:bg-gray-50 transition"
              >
                取消
              </button>
              <button
                onClick={handleEditSubmit}
                disabled={!editName.trim()}
                className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition disabled:opacity-50"
              >
                确定
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 摘要弹窗 */}
      {summaryDoc && (
        <div className="fixed inset-0 flex items-center justify-center z-50 backdrop-blur-sm bg-black/30">
          <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-lg">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-bold">📄 文档摘要</h2>
              <button
                onClick={() => setSummaryDoc(null)}
                className="text-gray-400 hover:text-gray-600 text-xl leading-none"
              >
                ✕
              </button>
            </div>
            <p className="text-sm text-gray-500 mb-4 truncate">{summaryDoc.name}</p>
            <div className="bg-gray-50 rounded p-4 max-h-80 overflow-y-auto">
              {summaryDoc.summary?.split('\n').map((line, i) => {
                const trimmed = line.trim();
                if (!trimmed) return null;
                return (
                  <p key={i} className="text-sm text-gray-700 mb-2 leading-relaxed">
                    {trimmed}
                  </p>
                );
              })}
            </div>
            <div className="flex justify-end mt-4">
              <button
                onClick={() => setSummaryDoc(null)}
                className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition"
              >
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default KnowledgeBase;