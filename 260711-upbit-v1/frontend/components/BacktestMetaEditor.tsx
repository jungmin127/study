'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Pencil } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { updateBacktestRun } from '@/lib/api/eda';
import { ApiError } from '@/lib/api/client';

interface BacktestMetaEditorProps {
  runId: string;
  title: string | null;
  description: string | null;
}

export default function BacktestMetaEditor({ runId, title, description }: BacktestMetaEditorProps) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [titleInput, setTitleInput] = useState(title ?? '');
  const [descriptionInput, setDescriptionInput] = useState(description ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function startEditing() {
    setTitleInput(title ?? '');
    setDescriptionInput(description ?? '');
    setError(null);
    setEditing(true);
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await updateBacktestRun(runId, {
        title: titleInput.trim() || null,
        description: descriptionInput.trim() || null,
      });
      setEditing(false);
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '저장에 실패했습니다.');
    } finally {
      setSaving(false);
    }
  }

  if (editing) {
    return (
      <div className="mb-2 max-w-md space-y-2">
        <Input
          value={titleInput}
          onChange={(e) => setTitleInput(e.target.value)}
          placeholder="제목"
          maxLength={200}
        />
        <Textarea
          value={descriptionInput}
          onChange={(e) => setDescriptionInput(e.target.value)}
          placeholder="설명"
          maxLength={2000}
        />
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex gap-2">
          <Button size="sm" onClick={handleSave} disabled={saving}>
            {saving ? '저장 중...' : '저장'}
          </Button>
          <Button size="sm" variant="outline" onClick={() => setEditing(false)} disabled={saving}>
            취소
          </Button>
        </div>
      </div>
    );
  }

  return (
    <button type="button" onClick={startEditing} className="group mb-2 flex items-start gap-1.5 text-left">
      <div>
        <p className="text-sm font-medium">
          {title || <span className="text-muted-foreground">(제목 없음)</span>}
        </p>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
      </div>
      <Pencil className="mt-0.5 size-3.5 text-muted-foreground opacity-0 group-hover:opacity-100" />
    </button>
  );
}
