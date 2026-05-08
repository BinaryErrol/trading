import { useOrders } from '../hooks/useApi';

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    Filled: 'bg-green-900/50 text-green-400',
    Cancelled: 'bg-red-900/50 text-red-400',
    Submitted: 'bg-blue-900/50 text-blue-400',
    PartiallyFilled: 'bg-yellow-900/50 text-yellow-400',
    Rejected: 'bg-red-900/50 text-red-400',
  };

  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${colors[status] || 'bg-gray-700 text-gray-300'}`}>
      {status}
    </span>
  );
}

function DirectionBadge({ direction }: { direction: string }) {
  const isBuy = direction === 'BUY';
  return (
    <span className={`font-medium ${isBuy ? 'text-green-400' : 'text-red-400'}`}>
      {direction}
    </span>
  );
}

export function OrderHistory() {
  const { data: orders, isLoading } = useOrders();

  if (isLoading || !orders) {
    return (
      <div className="bg-gray-800 rounded-lg p-6 animate-pulse">
        <div className="h-6 bg-gray-700 rounded w-1/4 mb-4" />
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-8 bg-gray-700 rounded" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <h2 className="text-lg font-semibold mb-4">Order History</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-gray-400 border-b border-gray-700">
              <th className="text-left py-2 px-3">Time</th>
              <th className="text-left py-2 px-3">Symbol</th>
              <th className="text-left py-2 px-3">Side</th>
              <th className="text-left py-2 px-3">Type</th>
              <th className="text-right py-2 px-3">Qty</th>
              <th className="text-right py-2 px-3">Fill Price</th>
              <th className="text-left py-2 px-3">Status</th>
              <th className="text-left py-2 px-3">Strategy</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((order) => (
              <tr key={order.id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                <td className="py-2 px-3 text-gray-400">
                  {new Date(order.submitted_at).toLocaleString(undefined, {
                    month: 'short',
                    day: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </td>
                <td className="py-2 px-3 font-medium">{order.symbol}</td>
                <td className="py-2 px-3">
                  <DirectionBadge direction={order.direction} />
                </td>
                <td className="py-2 px-3 text-gray-300">{order.order_type}</td>
                <td className="text-right py-2 px-3">
                  {order.filled_quantity}/{order.quantity}
                </td>
                <td className="text-right py-2 px-3">
                  {order.avg_fill_price ? `$${order.avg_fill_price.toFixed(2)}` : '—'}
                </td>
                <td className="py-2 px-3">
                  <StatusBadge status={order.status} />
                </td>
                <td className="py-2 px-3">
                  <span className="bg-gray-700 text-gray-300 px-2 py-0.5 rounded text-xs">
                    {order.strategy_name}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
