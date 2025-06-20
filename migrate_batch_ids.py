import os
from datetime import datetime, timezone
from app import app, db
from models import OperatorLog, QualityCheck, Batch, MachineDrawing

def migrate_batch_ids():
    with app.app_context():
        # Find all unique string batch_ids in OperatorLog
        unique_batch_ids = db.session.query(OperatorLog.batch_id).filter(db.func.length(OperatorLog.batch_id) > 0).distinct().all()
        unique_batch_ids = [bid[0] for bid in unique_batch_ids if not isinstance(bid[0], int)]
        print(f"Found {len(unique_batch_ids)} unique string batch_ids to migrate.")
        batch_id_map = {}
        for old_batch_id in unique_batch_ids:
            # Try to find a drawing for this batch (use first matching log)
            log = OperatorLog.query.filter_by(batch_id=old_batch_id).first()
            if not log or not log.drawing_id:
                print(f"Skipping batch_id {old_batch_id}: no drawing info.")
                continue
            # Use batch_quantity if present, else run_planned_quantity, else 1
            qty = getattr(log, 'batch_quantity', None)
            if qty is None:
                qty = getattr(log, 'run_planned_quantity', 1) or 1
            new_batch = Batch(drawing_id=log.drawing_id, created_at=log.created_at or datetime.now(timezone.utc), batch_quantity=qty)
            db.session.add(new_batch)
            db.session.flush()  # Get new_batch.id
            batch_id_map[old_batch_id] = new_batch.id
            # Update all OperatorLog rows
            OperatorLog.query.filter_by(batch_id=old_batch_id).update({'batch_id': new_batch.id})
            # Update all QualityCheck rows
            QualityCheck.query.filter_by(batch_id=old_batch_id).update({'batch_id': new_batch.id})
            print(f"Migrated batch_id {old_batch_id} -> {new_batch.id}")
        db.session.commit()
        print(f"Migration complete. {len(batch_id_map)} batch_ids migrated.")

if __name__ == '__main__':
    migrate_batch_ids() 