import { Routes, Route, Link } from 'react-router-dom';
import KnowledgeBase from './pages/KnowledgeBase';
import Chat from './pages/Chat';

function App() {
  return (
    <div className="min-h-screen bg-gray-50">
      <nav className="bg-white shadow-sm border-b">
        <div className="max-w-7xl mx-auto px-4 flex gap-6 py-3">
          <Link to="/" className="text-blue-600 font-medium hover:text-blue-800">
            知识库管理
          </Link>
          <Link to="/chat" className="text-blue-600 font-medium hover:text-blue-800">
            RAG
          </Link>
        </div>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={
            <div className="max-w-7xl mx-auto px-4 py-6">
              <KnowledgeBase />
            </div>
          } />
          <Route path="/chat" element={<Chat />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
